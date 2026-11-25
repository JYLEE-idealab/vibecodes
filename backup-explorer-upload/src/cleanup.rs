use std::collections::HashSet;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, Mutex};

use crate::scanner::{DuplicateGroup, FileEntry, LocationKind, ScanResult};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CleanupMethod {
    /// 지정한 임시 격리 보관함 폴더로 원래 폴더 구조를 유지한 채 이동
    MoveToQuarantine,
    /// 윈도우 휴지통으로 안전하게 이동
    MoveToRecycleBin,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SmartSelectRule {
    /// 파일명에 '복사본', '(1)', '(2)', 'copy' 등이 붙은 파일만 선택
    FilenameCopyPattern,
    /// 오래된 원본 1개만 남기고 나중에 생긴(최신 날짜) 사본 선택
    KeepOldestDeleteNewer,
    /// 최신 파일 1개만 남기고 오래된 날짜의 사본 선택
    KeepNewestDeleteOlder,
    /// 상위 폴더(얕은 경로) 1개만 남기고 깊은 하위 폴더의 사본 선택
    KeepShallowestDeleteDeeper,
    /// 세부 하위 폴더(깊은 경로) 1개만 남기고 상위 폴더의 사본 선택
    KeepDeepestDeleteShallower,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TargetScope {
    /// 외장하드 파일 중에서만 중복 정리 (내 PC 파일은 절대 건드리지 않음)
    ExternalOnly,
    /// 내 PC 파일 중에서만 중복 정리 (외장하드 파일은 절대 건드리지 않음)
    PcOnly,
}

#[derive(Debug, Clone, Default)]
pub struct CleanupProgress {
    pub is_running: bool,
    pub processed: usize,
    pub total: usize,
    pub current_file: String,
    pub finished_report: Option<CleanupExecutionReport>,
}

/// 파일명이 전형적인 복사본 패턴('(1)', '(2)', '- 복사본', 'copy' 등)인지 판별합니다.
pub fn is_copy_filename_pattern(name: &str) -> bool {
    let stem = Path::new(name)
        .file_stem()
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_else(|| name.to_lowercase());

    if stem.contains("복사본")
        || stem.contains("- copy")
        || stem.contains("_copy")
        || stem.ends_with(" copy")
        || stem.contains("사본")
    {
        return true;
    }

    let trimmed = stem.trim_end();
    if let Some(open_idx) = trimmed.rfind('(') {
        if trimmed.ends_with(')') && open_idx < trimmed.len() - 2 {
            let inside = &trimmed[open_idx + 1..trimmed.len() - 1];
            if !inside.is_empty() && inside.chars().all(|c| c.is_ascii_digit()) {
                return true;
            }
        }
    }

    false
}

/// 경로의 폴더 깊이(단계 수)를 계산합니다.
pub fn path_depth(path: &Path) -> usize {
    path.components().count()
}

/// 특정 파일을 삭제/격리 체크할 때, 동일 해시 그룹 내에 최소 1개의 보존 파일이 남는지 확인합니다.
pub fn can_select_file_safely(
    file_id: usize,
    files: &[FileEntry],
    currently_selected: &HashSet<usize>,
) -> bool {
    let Some(entry) = files.get(file_id) else {
        return false;
    };

    let mut group_ids = vec![entry.id];
    group_ids.extend(entry.matching_ids.iter().copied());

    let remaining_unselected = group_ids
        .iter()
        .filter(|&&id| id != file_id && !currently_selected.contains(&id))
        .count();

    if entry.location == LocationKind::External {
        let mut ext_total = 0usize;
        let mut ext_remaining = 0usize;
        for &id in &group_ids {
            if files[id].location == LocationKind::External {
                ext_total += 1;
                if id != file_id && !currently_selected.contains(&id) {
                    ext_remaining += 1;
                }
            }
        }
        if ext_total >= 2 && ext_remaining == 0 {
            return false;
        }
    }

    remaining_unselected >= 1
}

/// 주어진 중복 그룹 목록에 스마트 자동 선택 규칙을 고속으로 적용합니다.
pub fn apply_smart_selection(
    groups: &[&DuplicateGroup],
    files: &[FileEntry],
    scope: TargetScope,
    rule: SmartSelectRule,
    selected_set: &mut HashSet<usize>,
) -> usize {
    let mut added_count = 0usize;

    for g in groups {
        let candidate_ids: &[usize] = match scope {
            TargetScope::ExternalOnly => &g.ext_files,
            TargetScope::PcOnly => &g.pc_files,
        };

        if candidate_ids.len() < 2 {
            continue;
        }

        for &id in candidate_ids {
            selected_set.remove(&id);
        }

        match rule {
            SmartSelectRule::FilenameCopyPattern => {
                let mut pattern_matches: Vec<usize> = candidate_ids
                    .iter()
                    .copied()
                    .filter(|&id| is_copy_filename_pattern(&files[id].name))
                    .collect();

                // 만약 모든 후보가 패턴에 해당하면 가장 경로가 짧은 1개는 무조건 원본으로 남김
                if pattern_matches.len() >= candidate_ids.len() {
                    pattern_matches.sort_by_key(|&id| {
                        (
                            path_depth(&files[id].path),
                            files[id].path.as_os_str().len(),
                        )
                    });
                    if !pattern_matches.is_empty() {
                        pattern_matches.remove(0);
                    }
                }

                for id in pattern_matches {
                    selected_set.insert(id);
                    added_count += 1;
                }
            }
            SmartSelectRule::KeepOldestDeleteNewer => {
                let mut sorted = candidate_ids.to_vec();
                sorted.sort_by(|&a, &b| {
                    files[a]
                        .modified_ts
                        .cmp(&files[b].modified_ts)
                        .then_with(|| path_depth(&files[a].path).cmp(&path_depth(&files[b].path)))
                        .then_with(|| files[a].path.cmp(&files[b].path))
                });
                for &id in sorted.iter().skip(1) {
                    selected_set.insert(id);
                    added_count += 1;
                }
            }
            SmartSelectRule::KeepNewestDeleteOlder => {
                let mut sorted = candidate_ids.to_vec();
                sorted.sort_by(|&a, &b| {
                    files[b]
                        .modified_ts
                        .cmp(&files[a].modified_ts)
                        .then_with(|| path_depth(&files[a].path).cmp(&path_depth(&files[b].path)))
                        .then_with(|| files[a].path.cmp(&files[b].path))
                });
                for &id in sorted.iter().skip(1) {
                    selected_set.insert(id);
                    added_count += 1;
                }
            }
            SmartSelectRule::KeepShallowestDeleteDeeper => {
                let mut sorted = candidate_ids.to_vec();
                sorted.sort_by(|&a, &b| {
                    path_depth(&files[a].path)
                        .cmp(&path_depth(&files[b].path))
                        .then_with(|| {
                            is_copy_filename_pattern(&files[a].name)
                                .cmp(&is_copy_filename_pattern(&files[b].name))
                        })
                        .then_with(|| {
                            files[a]
                                .path
                                .as_os_str()
                                .len()
                                .cmp(&files[b].path.as_os_str().len())
                        })
                });
                for &id in sorted.iter().skip(1) {
                    selected_set.insert(id);
                    added_count += 1;
                }
            }
            SmartSelectRule::KeepDeepestDeleteShallower => {
                let mut sorted = candidate_ids.to_vec();
                sorted.sort_by(|&a, &b| {
                    path_depth(&files[b].path)
                        .cmp(&path_depth(&files[a].path))
                        .then_with(|| {
                            is_copy_filename_pattern(&files[a].name)
                                .cmp(&is_copy_filename_pattern(&files[b].name))
                        })
                });
                for &id in sorted.iter().skip(1) {
                    selected_set.insert(id);
                    added_count += 1;
                }
            }
        }
    }

    added_count
}

pub fn select_duplicates_in_subfolder(
    groups: &[&DuplicateGroup],
    files: &[FileEntry],
    scope: TargetScope,
    subfolder: &str,
    selected_set: &mut HashSet<usize>,
) -> usize {
    let mut added = 0usize;
    for g in groups {
        let candidate_ids: &[usize] = match scope {
            TargetScope::ExternalOnly => &g.ext_files,
            TargetScope::PcOnly => &g.pc_files,
        };
        if candidate_ids.len() < 2 {
            continue;
        }
        // 그룹 내에서 최소 1개는 선택되지 않은 상태로 남도록 보장
        let mut unselected_left = candidate_ids
            .iter()
            .filter(|id| !selected_set.contains(id))
            .count();

        for &id in candidate_ids {
            if unselected_left <= 1 {
                break;
            }
            if files[id].relative_folder == subfolder && !selected_set.contains(&id) {
                selected_set.insert(id);
                unselected_left -= 1;
                added += 1;
            }
        }
    }
    added
}

pub fn compute_quarantine_destination(quarantine_root: &Path, original_path: &Path) -> PathBuf {
    let mut dest = quarantine_root.to_path_buf();

    for comp in original_path.components() {
        match comp {
            Component::Prefix(prefix) => {
                let drive_name = prefix
                    .as_os_str()
                    .to_string_lossy()
                    .replace(':', "_드라이브")
                    .replace('\\', "");
                if !drive_name.is_empty() {
                    dest.push(drive_name);
                }
            }
            Component::Normal(part) => {
                dest.push(part);
            }
            _ => {}
        }
    }

    if !dest.exists() {
        return dest;
    }

    let parent = dest
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| quarantine_root.to_path_buf());
    let stem = dest
        .file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| "file".to_string());
    let ext = dest
        .extension()
        .map(|s| format!(".{}", s.to_string_lossy()))
        .unwrap_or_default();

    for idx in 1..10000 {
        let candidate = parent.join(format!("{}_중복{}{}", stem, idx, ext));
        if !candidate.exists() {
            return candidate;
        }
    }

    dest
}

#[cfg(target_os = "windows")]
pub fn move_files_to_recycle_bin_batch(paths: &[PathBuf]) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;

    if paths.is_empty() {
        return Ok(());
    }

    #[repr(C)]
    struct SHFILEOPSTRUCTW {
        hwnd: usize,
        w_func: u32,
        p_from: *const u16,
        p_to: *const u16,
        f_flags: u16,
        f_any_operations_aborted: i32,
        h_name_mappings: usize,
        lpsz_progress_title: *const u16,
    }

    #[link(name = "shell32")]
    extern "system" {
        fn SHFileOperationW(lpFileOp: *mut SHFILEOPSTRUCTW) -> i32;
    }

    #[link(name = "ole32")]
    extern "system" {
        fn CoInitializeEx(pvReserved: *mut std::ffi::c_void, dwCoInit: u32) -> i32;
    }

    const FO_DELETE: u32 = 0x0003;
    const FOF_SILENT: u16 = 0x0004;
    const FOF_NOCONFIRMATION: u16 = 0x0010;
    const FOF_ALLOWUNDO: u16 = 0x0040;
    const FOF_NOERRORUI: u16 = 0x0400;

    let mut wide_buffer: Vec<u16> = Vec::new();
    for path in paths {
        let raw_str = path.to_string_lossy().replace('/', "\\");
        let clean_str = raw_str.strip_prefix(r"\\?\").unwrap_or(&raw_str);
        wide_buffer.extend(Path::new(clean_str).as_os_str().encode_wide());
        wide_buffer.push(0);
    }
    wide_buffer.push(0);

    let mut op = SHFILEOPSTRUCTW {
        hwnd: 0,
        w_func: FO_DELETE,
        p_from: wide_buffer.as_ptr(),
        p_to: std::ptr::null(),
        f_flags: FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT,
        f_any_operations_aborted: 0,
        h_name_mappings: 0,
        lpsz_progress_title: std::ptr::null(),
    };

    let ret = unsafe {
        let _ = CoInitializeEx(std::ptr::null_mut(), 0x2); // STA 초기화 보장
        SHFileOperationW(&mut op)
    };
    if ret == 0 && op.f_any_operations_aborted == 0 {
        Ok(())
    } else {
        Err(format!("휴지통 이동 실패 (오류 코드: 0x{:X})", ret))
    }
}

#[derive(Debug, Clone)]
pub struct CleanupExecutionReport {
    pub success_ids: HashSet<usize>,
    pub success_count: usize,
    pub failed_count: usize,
    pub freed_bytes: u64,
    pub first_error: Option<String>,
    pub quarantine_dir_used: Option<PathBuf>,
}

/// 백그라운드 스레드에서 안전하게 파일 이동/휴지통 전송을 수행합니다 (메인 UI 멈춤 완전 방지).
pub fn run_cleanup_background(
    targets: Vec<(usize, PathBuf, u64, String)>,
    method: CleanupMethod,
    quarantine_root: PathBuf,
    progress_arc: Arc<Mutex<CleanupProgress>>,
) {
    let total = targets.len();
    let mut success_ids: HashSet<usize> = HashSet::new();
    let mut failed_count = 0usize;
    let mut freed_bytes = 0u64;
    let mut first_error = None;

    match method {
        CleanupMethod::MoveToQuarantine => {
            for (idx, (file_id, path, size, name)) in targets.into_iter().enumerate() {
                if let Ok(mut p) = progress_arc.lock() {
                    p.processed = idx;
                    p.total = total;
                    p.current_file = name.clone();
                }

                if !path.exists() {
                    continue;
                }

                let dest = compute_quarantine_destination(&quarantine_root, &path);
                let res = if let Some(parent) = dest.parent() {
                    if let Err(e) = fs::create_dir_all(parent) {
                        Err(format!("격리 폴더 생성 실패: {}", e))
                    } else {
                        match fs::rename(&path, &dest) {
                            Ok(_) => Ok(()),
                            Err(_) => match fs::copy(&path, &dest) {
                                Ok(_) => fs::remove_file(&path)
                                    .map_err(|e| format!("원본 정리 실패: {}", e)),
                                Err(e) => Err(format!("격리 폴더 이동 실패: {}", e)),
                            },
                        }
                    }
                } else {
                    Err("잘못된 격리 폴더 경로".to_string())
                };

                match res {
                    Ok(_) => {
                        success_ids.insert(file_id);
                        freed_bytes += size;
                    }
                    Err(err_msg) => {
                        failed_count += 1;
                        if first_error.is_none() {
                            first_error = Some(format!("{}: {}", name, err_msg));
                        }
                    }
                }
            }
        }
        CleanupMethod::MoveToRecycleBin => {
            for (chunk_idx, chunk) in targets.chunks(32).enumerate() {
                if let Ok(mut p) = progress_arc.lock() {
                    p.processed = chunk_idx * 32;
                    p.total = total;
                    if let Some((_, _, _, first_name)) = chunk.first() {
                        p.current_file = first_name.clone();
                    }
                }

                let paths: Vec<PathBuf> = chunk
                    .iter()
                    .filter(|(_, p, _, _)| p.exists())
                    .map(|(_, p, _, _)| p.clone())
                    .collect();

                match move_files_to_recycle_bin_batch(&paths) {
                    Ok(_) => {
                        for (fid, _, sz, _) in chunk {
                            success_ids.insert(*fid);
                            freed_bytes += *sz;
                        }
                    }
                    Err(e) => {
                        failed_count += chunk.len();
                        if first_error.is_none() {
                            first_error = Some(e);
                        }
                    }
                }
            }
        }
    }

    let report = CleanupExecutionReport {
        success_count: success_ids.len(),
        success_ids,
        failed_count,
        freed_bytes,
        first_error,
        quarantine_dir_used: if method == CleanupMethod::MoveToQuarantine {
            Some(quarantine_root)
        } else {
            None
        },
    };

    if let Ok(mut p) = progress_arc.lock() {
        p.processed = total;
        p.is_running = false;
        p.finished_report = Some(report);
    }
}

/// 백그라운드 정리가 완료된 후 ScanResult 메모리 상태를 O(N)으로 빠르게 갱신합니다.
pub fn apply_cleanup_report_to_result(result: &mut ScanResult, success_ids: &HashSet<usize>) {
    if success_ids.is_empty() {
        return;
    }

    // 중복 매칭이 있던 파일들만 빠르게 갱신
    for file in &mut result.files {
        if !file.matching_ids.is_empty() {
            file.matching_ids.retain(|id| !success_ids.contains(id));
        }
    }

    let update_groups = |groups: &mut Vec<DuplicateGroup>, is_pc_wasted: bool| {
        for g in groups.iter_mut() {
            g.pc_files.retain(|id| !success_ids.contains(id));
            g.ext_files.retain(|id| !success_ids.contains(id));
            if is_pc_wasted {
                g.wasted_ext_bytes = (g.pc_files.len().saturating_sub(1) as u64) * g.file_size;
            } else {
                g.wasted_ext_bytes = (g.ext_files.len().saturating_sub(1) as u64) * g.file_size;
            }
        }
    };

    update_groups(&mut result.ext_duplicate_groups, false);
    result
        .ext_duplicate_groups
        .retain(|g| g.ext_files.len() >= 2);

    update_groups(&mut result.pc_duplicate_groups, true);
    result.pc_duplicate_groups.retain(|g| g.pc_files.len() >= 2);

    update_groups(&mut result.cross_duplicate_groups, false);
    result
        .cross_duplicate_groups
        .retain(|g| !g.pc_files.is_empty() && !g.ext_files.is_empty());

    result
        .unbacked_pc_ids
        .retain(|id| !success_ids.contains(id));
    result
        .backed_up_pc_ids
        .retain(|id| !success_ids.contains(id));
    result.ext_only_ids.retain(|id| !success_ids.contains(id));

    result.summary.ext_duplicate_groups = result.ext_duplicate_groups.len();
    result.summary.ext_duplicate_extra_files = result
        .ext_duplicate_groups
        .iter()
        .map(|g| g.ext_files.len().saturating_sub(1))
        .sum();
    result.summary.ext_wasted_bytes = result
        .ext_duplicate_groups
        .iter()
        .map(|g| g.wasted_ext_bytes)
        .sum();

    result.summary.pc_duplicate_groups = result.pc_duplicate_groups.len();
    result.summary.pc_duplicate_extra_files = result
        .pc_duplicate_groups
        .iter()
        .map(|g| g.pc_files.len().saturating_sub(1))
        .sum();
    result.summary.pc_wasted_bytes = result
        .pc_duplicate_groups
        .iter()
        .map(|g| g.wasted_ext_bytes)
        .sum();

    result.summary.cross_duplicate_groups = result.cross_duplicate_groups.len();
}
