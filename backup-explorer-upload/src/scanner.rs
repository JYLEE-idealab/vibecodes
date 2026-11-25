use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime};

use chrono::{DateTime, Local};
use rayon::prelude::*;
use walkdir::{DirEntry, WalkDir};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum LocationKind {
    Pc,
    External,
}

impl LocationKind {
    pub fn label(&self) -> &'static str {
        match self {
            LocationKind::Pc => "내 PC",
            LocationKind::External => "외장하드",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FileCategory {
    All,
    Photo,
    Video,
    Document,
    Audio,
    Archive,
    Other,
}

impl FileCategory {
    pub fn from_extension(ext: &str) -> Self {
        let lower = ext.to_ascii_lowercase();
        match lower.as_str() {
            "jpg" | "jpeg" | "png" | "gif" | "bmp" | "webp" | "heic" | "heif" | "raw" | "cr2"
            | "cr3" | "nef" | "arw" | "dng" | "tif" | "tiff" | "svg" | "ico" | "psd" | "ai"
            | "jfif" => FileCategory::Photo,
            "mp4" | "mkv" | "avi" | "mov" | "wmv" | "flv" | "webm" | "m4v" | "mpg" | "mpeg"
            | "3gp" | "ts" | "mts" | "m2ts" | "vob" | "asf" => FileCategory::Video,
            "pdf" | "doc" | "docx" | "xls" | "xlsx" | "ppt" | "pptx" | "hwp" | "hwpx" | "txt"
            | "md" | "csv" | "rtf" | "odt" | "ods" | "odp" | "epub" | "json" | "xml" => {
                FileCategory::Document
            }
            "mp3" | "wav" | "flac" | "aac" | "ogg" | "wma" | "m4a" | "opus" | "aiff" => {
                FileCategory::Audio
            }
            "zip" | "rar" | "7z" | "tar" | "gz" | "bz2" | "xz" | "iso" | "alz" | "egg" => {
                FileCategory::Archive
            }
            _ => FileCategory::Other,
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            FileCategory::All => "전체 파일",
            FileCategory::Photo => "사진 (이미지)",
            FileCategory::Video => "동영상",
            FileCategory::Document => "문서",
            FileCategory::Audio => "오디오 (음악)",
            FileCategory::Archive => "압축 파일",
            FileCategory::Other => "기타 파일",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackupStatus {
    /// PC에만 있고 외장하드에는 없음 (백업 필요)
    UnbackedOnPc,
    /// PC와 외장하드 양쪽에 모두 존재함 (백업 완료)
    BackedUp,
    /// 외장하드에만 존재함 (PC에는 없음)
    ExternalOnly,
}

impl BackupStatus {
    pub fn label(&self) -> &'static str {
        match self {
            BackupStatus::UnbackedOnPc => "미백업 (PC에만 있음)",
            BackupStatus::BackedUp => "백업 완료 (양쪽 존재)",
            BackupStatus::ExternalOnly => "외장하드 전용",
        }
    }
}

#[derive(Debug, Clone)]
pub struct FileEntry {
    pub id: usize,
    pub path: PathBuf,
    pub relative_folder: String,
    pub name: String,
    pub extension: String,
    pub category: FileCategory,
    pub size: u64,
    pub modified_str: String,
    pub modified_ts: i64,
    pub location: LocationKind,
    pub hash: Option<String>,
    pub status: BackupStatus,
    pub pc_copy_count: usize,
    pub ext_copy_count: usize,
    pub matching_ids: Vec<usize>,
}

#[derive(Debug, Clone)]
pub struct DuplicateGroup {
    pub hash: String,
    pub file_size: u64,
    #[allow(dead_code)]
    pub category: FileCategory,
    pub representative_name: String,
    pub pc_files: Vec<usize>,
    pub ext_files: Vec<usize>,
    /// 외장하드(또는 PC) 내부에서 중복으로 낭비되는 용량
    pub wasted_ext_bytes: u64,
}

#[derive(Debug, Clone, Default)]
pub struct ScanSummary {
    pub total_pc_files: usize,
    pub total_pc_bytes: u64,
    pub total_ext_files: usize,
    pub total_ext_bytes: u64,

    pub unbacked_pc_files: usize,
    pub unbacked_pc_bytes: u64,

    pub backed_up_pc_files: usize,
    pub backed_up_pc_bytes: u64,

    pub ext_only_files: usize,
    pub ext_only_bytes: u64,

    pub ext_duplicate_groups: usize,
    pub ext_duplicate_extra_files: usize,
    pub ext_wasted_bytes: u64,

    pub pc_duplicate_groups: usize,
    pub pc_duplicate_extra_files: usize,
    pub pc_wasted_bytes: u64,

    pub cross_duplicate_groups: usize,
    pub elapsed_secs: f64,
}

#[derive(Debug, Clone, Default)]
pub struct ScanResult {
    pub files: Vec<FileEntry>,
    pub unbacked_pc_ids: Vec<usize>,
    pub backed_up_pc_ids: Vec<usize>,
    pub ext_only_ids: Vec<usize>,
    pub ext_duplicate_groups: Vec<DuplicateGroup>,
    pub pc_duplicate_groups: Vec<DuplicateGroup>,
    pub cross_duplicate_groups: Vec<DuplicateGroup>,
    pub summary: ScanSummary,
    pub pc_folders: Vec<String>,
    pub ext_folders: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScanStage {
    Idle,
    DiscoveringFiles,
    GroupingSizes,
    FastPartialHashing,
    FullContentHashing,
    ClassifyingResults,
    Completed,
    Cancelled,
}

impl ScanStage {
    pub fn label(&self) -> &'static str {
        match self {
            ScanStage::Idle => "대기 중",
            ScanStage::DiscoveringFiles => "1단계: 디렉토리 및 파일 목록 고속 탐색 중...",
            ScanStage::GroupingSizes => "2단계: 파일 크기별 고유 파일 즉시 분류 중...",
            ScanStage::FastPartialHashing => {
                "3단계: [SSD 병렬 + 외장하드 순차] 고속 샘플 해시(앞/중간/뒤 48KB) 비교 중..."
            }
            ScanStage::FullContentHashing => {
                "4단계: [SSD 병렬 + 외장하드 순차] 전체 바이트 정밀 해시 대조 중..."
            }
            ScanStage::ClassifyingResults => "결과 정리 및 중복 분석 중...",
            ScanStage::Completed => "분석 완료",
            ScanStage::Cancelled => "사용자에 의해 중단됨",
        }
    }
}

#[derive(Debug, Clone)]
pub struct ScanProgress {
    pub stage: ScanStage,
    pub discovered_pc_files: usize,
    pub discovered_ext_files: usize,
    pub discovered_bytes: u64,
    pub current_file: String,
    pub processed_files: usize,
    pub total_files_to_process: usize,
    pub processed_bytes: u64,
    pub total_bytes_to_process: u64,
    pub percentage: f32,
    pub speed_bytes_per_sec: f64,
    pub eta_seconds: Option<u64>,
    pub elapsed_seconds: f64,
}

impl Default for ScanProgress {
    fn default() -> Self {
        Self {
            stage: ScanStage::Idle,
            discovered_pc_files: 0,
            discovered_ext_files: 0,
            discovered_bytes: 0,
            current_file: String::new(),
            processed_files: 0,
            total_files_to_process: 0,
            processed_bytes: 0,
            total_bytes_to_process: 0,
            percentage: 0.0,
            speed_bytes_per_sec: 0.0,
            eta_seconds: None,
            elapsed_seconds: 0.0,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct ScanOptions {
    pub skip_zero_byte: bool,
    /// Windows, Program Files, AppData, node_modules 등 시스템/캐시 폴더 자동 건너뛰기
    pub skip_system_folders: bool,
    /// 초고속 샘플 해시 모드 (크기 + 앞/중간/뒤 48KB 해시만으로 판별하여 대용량 영상 전체 읽기 생략)
    pub fast_sample_mode: bool,
}

impl Default for ScanOptions {
    fn default() -> Self {
        Self {
            skip_zero_byte: true,
            skip_system_folders: true,
            fast_sample_mode: true,
        }
    }
}

pub fn format_size(bytes: u64) -> String {
    const KB: f64 = 1024.0;
    const MB: f64 = KB * 1024.0;
    const GB: f64 = MB * 1024.0;
    const TB: f64 = GB * 1024.0;

    let b = bytes as f64;
    if b >= TB {
        format!("{:.2} TB", b / TB)
    } else if b >= GB {
        format!("{:.2} GB", b / GB)
    } else if b >= MB {
        format!("{:.1} MB", b / MB)
    } else if b >= KB {
        format!("{:.1} KB", b / KB)
    } else {
        format!("{} B", bytes)
    }
}

pub fn format_duration(secs: u64) -> String {
    if secs >= 3600 {
        let h = secs / 3600;
        let m = (secs % 3600) / 60;
        let s = secs % 60;
        format!("{}시간 {}분 {}초", h, m, s)
    } else if secs >= 60 {
        let m = secs / 60;
        let s = secs % 60;
        format!("{}분 {}초", m, s)
    } else {
        format!("{}초", secs)
    }
}

fn format_system_time(st: Option<SystemTime>) -> (String, i64) {
    if let Some(t) = st {
        let dt: DateTime<Local> = t.into();
        (dt.format("%Y-%m-%d %H:%M").to_string(), dt.timestamp())
    } else {
        ("-".to_string(), 0)
    }
}

/// 디렉토리 트리 순회 시 백업과 무관한 시스템/프로그램/개발 캐시 폴더는 아예 하위 진입조차 하지 않도록 즉시 차단합니다.
fn should_enter_dir(entry: &DirEntry, is_root: bool, skip_system_folders: bool) -> bool {
    if is_root || !entry.file_type().is_dir() {
        return true;
    }

    let name = entry.file_name().to_string_lossy();
    let lower = name.to_ascii_lowercase();

    // 필수 시스템 보호 폴더 및 임시 격리 보관함은 항상 제외
    if matches!(
        lower.as_str(),
        "$recycle.bin"
            | "system volume information"
            | "$windows.~bt"
            | "$windows.~ws"
            | "found.000"
            | "found.001"
            | "config.msi"
            | "_중복격리보관함"
            | ".spotlight-v100"
            | ".temporaryitems"
            | ".trashes"
    ) {
        return false;
    }

    if !skip_system_folders {
        return true;
    }

    // 점(.)으로 시작하는 숨김/개발 캐시 폴더 (.git, .cargo, .rustup, .vscode, .gemini 등) 제외
    if lower.starts_with('.') && lower.len() > 1 {
        return false;
    }

    // 윈도우 시스템 폴더, 설치된 프로그램 폴더, AppData 및 개발 빌드 캐시 폴더 제외
    !matches!(
        lower.as_str(),
        "windows"
            | "program files"
            | "program files (x86)"
            | "programdata"
            | "appdata"
            | "msocache"
            | "perflogs"
            | "recovery"
            | "intel"
            | "nvidia"
            | "amd"
            | "node_modules"
            | "bower_components"
            | "__pycache__"
            | "venv"
            | "env"
            | "site-packages"
            | "cargo-target"
            | "cmake-build-debug"
            | "cmake-build-release"
    )
}

/// 고속 부분 해시 계산: 파일이 48KB 이하이면 전체를 읽고,
/// 그보다 크면 앞 16KB + 중간 16KB + 끝 16KB를 읽어 해시를 계산합니다.
fn compute_partial_hash(path: &Path, size: u64) -> std::io::Result<[u8; 32]> {
    let mut file = File::open(path)?;
    let mut hasher = blake3::Hasher::new();
    hasher.update(&size.to_le_bytes());

    const CHUNK: u64 = 16 * 1024;
    if size <= CHUNK * 3 {
        let mut buf = Vec::with_capacity(size as usize);
        file.read_to_end(&mut buf)?;
        hasher.update(&buf);
    } else {
        let mut buf = vec![0u8; CHUNK as usize];

        // 1. 앞 16KB
        file.read_exact(&mut buf)?;
        hasher.update(&buf);

        // 2. 중간 16KB
        file.seek(SeekFrom::Start(size / 2 - CHUNK / 2))?;
        file.read_exact(&mut buf)?;
        hasher.update(&buf);

        // 3. 끝 16KB
        file.seek(SeekFrom::End(-(CHUNK as i64)))?;
        file.read_exact(&mut buf)?;
        hasher.update(&buf);
    }

    Ok(*hasher.finalize().as_bytes())
}

/// 전체 파일 해시 계산 (1MB 대구경 버퍼 스트리밍으로 HDD/SSD 처리량 극대화)
fn compute_full_hash(
    path: &Path,
    cancel_flag: &AtomicBool,
    bytes_counter: &AtomicU64,
) -> std::io::Result<Option<String>> {
    let mut file = File::open(path)?;
    let mut hasher = blake3::Hasher::new();
    let mut buf = vec![0u8; 1024 * 1024]; // 1MB 버퍼

    loop {
        if cancel_flag.load(Ordering::Relaxed) {
            return Ok(None);
        }
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
        bytes_counter.fetch_add(n as u64, Ordering::Relaxed);
    }

    Ok(Some(hasher.finalize().to_hex().to_string()))
}

pub fn run_scan(
    pc_roots: Vec<PathBuf>,
    ext_roots: Vec<PathBuf>,
    options: ScanOptions,
    progress_arc: Arc<Mutex<ScanProgress>>,
    cancel_flag: Arc<AtomicBool>,
) -> Option<ScanResult> {
    let start_instant = Instant::now();

    // 1. 파일 탐색 단계
    {
        if let Ok(mut p) = progress_arc.lock() {
            *p = ScanProgress {
                stage: ScanStage::DiscoveringFiles,
                percentage: 2.0,
                ..Default::default()
            };
        }
    }

    let mut files: Vec<FileEntry> = Vec::new();
    let mut discovered_pc = 0usize;
    let mut discovered_ext = 0usize;
    let mut discovered_bytes = 0u64;
    let mut last_ui_update = Instant::now();

    let all_roots: Vec<(PathBuf, LocationKind)> = pc_roots
        .into_iter()
        .map(|p| (p, LocationKind::Pc))
        .chain(ext_roots.into_iter().map(|p| (p, LocationKind::External)))
        .collect();

    for (root, location) in &all_roots {
        let walker = WalkDir::new(root)
            .follow_links(false)
            .into_iter()
            .filter_entry(|e| should_enter_dir(e, e.depth() == 0, options.skip_system_folders));

        for entry_res in walker {
            if cancel_flag.load(Ordering::Relaxed) {
                if let Ok(mut p) = progress_arc.lock() {
                    p.stage = ScanStage::Cancelled;
                }
                return None;
            }

            let entry = match entry_res {
                Ok(e) => e,
                Err(_) => continue,
            };

            if !entry.file_type().is_file() {
                continue;
            }

            let meta = match entry.metadata() {
                Ok(m) => m,
                Err(_) => continue,
            };

            let size = meta.len();
            if options.skip_zero_byte && size == 0 {
                continue;
            }

            let path = entry.path().to_path_buf();
            let name = path
                .file_name()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default();

            if name.eq_ignore_ascii_case("desktop.ini")
                || name.eq_ignore_ascii_case("Thumbs.db")
                || name.eq_ignore_ascii_case(".DS_Store")
                || name.eq_ignore_ascii_case("pagefile.sys")
                || name.eq_ignore_ascii_case("hiberfil.sys")
                || name.eq_ignore_ascii_case("swapfile.sys")
            {
                continue;
            }

            let extension = path
                .extension()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default();
            let category = FileCategory::from_extension(&extension);
            let (modified_str, modified_ts) = format_system_time(meta.modified().ok());

            let relative_folder = path
                .parent()
                .and_then(|parent| parent.strip_prefix(root).ok())
                .map(|rel| {
                    let s = rel.to_string_lossy().to_string();
                    if s.is_empty() {
                        "\\".to_string()
                    } else {
                        format!("\\{}", s)
                    }
                })
                .unwrap_or_else(|| "\\".to_string());

            let id = files.len();
            files.push(FileEntry {
                id,
                path,
                relative_folder,
                name: name.clone(),
                extension,
                category,
                size,
                modified_str,
                modified_ts,
                location: *location,
                hash: None,
                status: match location {
                    LocationKind::Pc => BackupStatus::UnbackedOnPc,
                    LocationKind::External => BackupStatus::ExternalOnly,
                },
                pc_copy_count: if *location == LocationKind::Pc { 1 } else { 0 },
                ext_copy_count: if *location == LocationKind::External { 1 } else { 0 },
                matching_ids: Vec::new(),
            });

            match location {
                LocationKind::Pc => discovered_pc += 1,
                LocationKind::External => discovered_ext += 1,
            }
            discovered_bytes += size;

            if last_ui_update.elapsed() >= Duration::from_millis(80) {
                if let Ok(mut p) = progress_arc.lock() {
                    p.discovered_pc_files = discovered_pc;
                    p.discovered_ext_files = discovered_ext;
                    p.discovered_bytes = discovered_bytes;
                    p.current_file = name;
                    p.elapsed_seconds = start_instant.elapsed().as_secs_f64();
                    p.percentage = 10.0;
                }
                last_ui_update = Instant::now();
            }
        }
    }

    // 2. 파일 크기별 그룹화 (크기가 유일한 파일은 해시 계산 즉시 생략!)
    {
        if let Ok(mut p) = progress_arc.lock() {
            p.stage = ScanStage::GroupingSizes;
            p.discovered_pc_files = discovered_pc;
            p.discovered_ext_files = discovered_ext;
            p.discovered_bytes = discovered_bytes;
            p.percentage = 15.0;
            p.elapsed_seconds = start_instant.elapsed().as_secs_f64();
        }
    }

    let mut size_groups: HashMap<u64, Vec<usize>> = HashMap::new();
    for file in &files {
        size_groups.entry(file.size).or_default().push(file.id);
    }

    // 크기가 같은 파일이 2개 이상인 그룹만 부분 해시 후보로 추출
    let mut pc_partial_candidates: Vec<usize> = Vec::new();
    let mut ext_partial_candidates: Vec<usize> = Vec::new();

    for ids in size_groups.values().filter(|ids| ids.len() >= 2) {
        for &id in ids {
            match files[id].location {
                LocationKind::Pc => pc_partial_candidates.push(id),
                LocationKind::External => ext_partial_candidates.push(id),
            }
        }
    }

    // 외장하드(HDD)의 물리 헤드 이동을 최소화하기 위해 디스크 경로 순서대로 정렬!
    ext_partial_candidates.sort_by(|&a, &b| files[a].path.cmp(&files[b].path));

    // 3. 고속 부분 해시 (내 PC SSD는 멀티스레드 병렬 + 외장하드 HDD는 단일 스레드 순차 읽기 동시 진행!)
    let total_partial = pc_partial_candidates.len() + ext_partial_candidates.len();
    {
        if let Ok(mut p) = progress_arc.lock() {
            p.stage = ScanStage::FastPartialHashing;
            p.processed_files = 0;
            p.total_files_to_process = total_partial;
            p.percentage = 18.0;
            p.elapsed_seconds = start_instant.elapsed().as_secs_f64();
        }
    }

    let partial_stage_start = Instant::now();
    let partial_done_count = AtomicUsize::new(0);
    let end_pct_for_partial = if options.fast_sample_mode { 95.0 } else { 45.0 };

    let update_partial_progress = |done: usize, name: &str| {
        if done % 16 == 0 || done == total_partial {
            if let Ok(mut p) = progress_arc.lock() {
                p.processed_files = done;
                p.current_file = name.to_string();
                let frac = if total_partial > 0 {
                    done as f32 / total_partial as f32
                } else {
                    1.0
                };
                p.percentage = 18.0 + frac * (end_pct_for_partial - 18.0);
                p.elapsed_seconds = start_instant.elapsed().as_secs_f64();

                let stage_elapsed = partial_stage_start.elapsed().as_secs_f64();
                if stage_elapsed > 0.3 && done > 0 {
                    let remaining_files = total_partial.saturating_sub(done);
                    let files_per_sec = done as f64 / stage_elapsed;
                    if files_per_sec > 0.0 {
                        p.eta_seconds = Some((remaining_files as f64 / files_per_sec) as u64);
                    }
                }
            }
        }
    };

    let (pc_partial_res, ext_partial_res) = std::thread::scope(|s| {
        // 스레드 1: 외장하드(HDD) 전용 단일 스레드 순차 읽기 (헤드 스래싱 원천 차단!)
        let ext_handle = s.spawn(|| {
            let mut out = Vec::with_capacity(ext_partial_candidates.len());
            for &file_id in &ext_partial_candidates {
                if cancel_flag.load(Ordering::Relaxed) {
                    break;
                }
                let entry = &files[file_id];
                let res = compute_partial_hash(&entry.path, entry.size).ok();
                let done = partial_done_count.fetch_add(1, Ordering::Relaxed) + 1;
                update_partial_progress(done, &entry.name);
                out.push((file_id, res));
            }
            out
        });

        // 메인 스레드 풀: 내 PC(NVMe SSD) 멀티스레드 병렬 읽기
        let pc_out: Vec<(usize, Option<[u8; 32]>)> = pc_partial_candidates
            .par_iter()
            .map(|&file_id| {
                if cancel_flag.load(Ordering::Relaxed) {
                    return (file_id, None);
                }
                let entry = &files[file_id];
                let res = compute_partial_hash(&entry.path, entry.size).ok();
                let done = partial_done_count.fetch_add(1, Ordering::Relaxed) + 1;
                update_partial_progress(done, &entry.name);
                (file_id, res)
            })
            .collect();

        let ext_out = ext_handle.join().unwrap_or_default();
        (pc_out, ext_out)
    });

    if cancel_flag.load(Ordering::Relaxed) {
        if let Ok(mut p) = progress_arc.lock() {
            p.stage = ScanStage::Cancelled;
        }
        return None;
    }

    // (파일 크기, 부분 해시)가 모두 일치하는 그룹 분류
    let mut partial_groups: HashMap<(u64, [u8; 32]), Vec<usize>> = HashMap::new();
    for (file_id, opt_hash) in pc_partial_res.into_iter().chain(ext_partial_res) {
        if let Some(ph) = opt_hash {
            let sz = files[file_id].size;
            partial_groups.entry((sz, ph)).or_default().push(file_id);
        }
    }

    let mut pc_full_candidates: Vec<usize> = Vec::new();
    let mut ext_full_candidates: Vec<usize> = Vec::new();
    let mut already_hashed: Vec<(usize, String)> = Vec::new();

    for ((sz, ph), group_ids) in partial_groups {
        if group_ids.len() >= 2 {
            // 초고속 샘플 모드이거나 파일 크기가 48KB 이하인 경우 이미 계산된 해시를 즉시 확정!
            if options.fast_sample_mode || sz <= 48 * 1024 {
                let hex_str = blake3::Hash::from_bytes(ph).to_hex().to_string();
                for id in group_ids {
                    already_hashed.push((id, hex_str.clone()));
                }
            } else {
                for id in group_ids {
                    match files[id].location {
                        LocationKind::Pc => pc_full_candidates.push(id),
                        LocationKind::External => ext_full_candidates.push(id),
                    }
                }
            }
        }
    }

    for (id, h) in already_hashed {
        files[id].hash = Some(h);
    }

    // 4. 정밀 전체 내용 해시 (초고속 모드가 꺼져 있을 때만 실행, 이 역시 SSD 병렬 + HDD 순차로 동작!)
    let total_full_files = pc_full_candidates.len() + ext_full_candidates.len();
    if total_full_files > 0 {
        ext_full_candidates.sort_by(|&a, &b| files[a].path.cmp(&files[b].path));

        let total_full_bytes: u64 = pc_full_candidates
            .iter()
            .chain(ext_full_candidates.iter())
            .map(|&id| files[id].size)
            .sum();

        {
            if let Ok(mut p) = progress_arc.lock() {
                p.stage = ScanStage::FullContentHashing;
                p.processed_files = 0;
                p.total_files_to_process = total_full_files;
                p.processed_bytes = 0;
                p.total_bytes_to_process = total_full_bytes;
                p.percentage = 45.0;
                p.eta_seconds = None;
                p.elapsed_seconds = start_instant.elapsed().as_secs_f64();
            }
        }

        let full_stage_start = Instant::now();
        let full_done_files = AtomicUsize::new(0);
        let full_done_bytes = AtomicU64::new(0);

        let update_full_progress = |done_f: usize, name: &str| {
            let done_b = full_done_bytes.load(Ordering::Relaxed);
            if let Ok(mut p) = progress_arc.lock() {
                p.processed_files = done_f;
                p.processed_bytes = done_b;
                p.current_file = name.to_string();
                p.elapsed_seconds = start_instant.elapsed().as_secs_f64();

                let frac = if total_full_bytes > 0 {
                    (done_b as f64 / total_full_bytes as f64).clamp(0.0, 1.0) as f32
                } else {
                    (done_f as f32 / total_full_files as f32).clamp(0.0, 1.0)
                };
                p.percentage = 45.0 + frac * 50.0;

                let stage_elapsed = full_stage_start.elapsed().as_secs_f64();
                if stage_elapsed > 0.25 && done_b > 0 {
                    let speed = done_b as f64 / stage_elapsed;
                    p.speed_bytes_per_sec = speed;
                    let remaining_bytes = total_full_bytes.saturating_sub(done_b);
                    if speed > 0.0 {
                        p.eta_seconds = Some((remaining_bytes as f64 / speed) as u64);
                    }
                }
            }
        };

        let (pc_full_res, ext_full_res) = std::thread::scope(|s| {
            let ext_handle = s.spawn(|| {
                let mut out = Vec::with_capacity(ext_full_candidates.len());
                for &file_id in &ext_full_candidates {
                    if cancel_flag.load(Ordering::Relaxed) {
                        break;
                    }
                    let entry = &files[file_id];
                    let hash_opt = compute_full_hash(&entry.path, &cancel_flag, &full_done_bytes)
                        .ok()
                        .flatten();
                    let done_f = full_done_files.fetch_add(1, Ordering::Relaxed) + 1;
                    update_full_progress(done_f, &entry.name);
                    out.push((file_id, hash_opt));
                }
                out
            });

            let pc_out: Vec<(usize, Option<String>)> = pc_full_candidates
                .par_iter()
                .map(|&file_id| {
                    if cancel_flag.load(Ordering::Relaxed) {
                        return (file_id, None);
                    }
                    let entry = &files[file_id];
                    let hash_opt = compute_full_hash(&entry.path, &cancel_flag, &full_done_bytes)
                        .ok()
                        .flatten();
                    let done_f = full_done_files.fetch_add(1, Ordering::Relaxed) + 1;
                    update_full_progress(done_f, &entry.name);
                    (file_id, hash_opt)
                })
                .collect();

            let ext_out = ext_handle.join().unwrap_or_default();
            (pc_out, ext_out)
        });

        if cancel_flag.load(Ordering::Relaxed) {
            if let Ok(mut p) = progress_arc.lock() {
                p.stage = ScanStage::Cancelled;
            }
            return None;
        }

        for (file_id, hash_opt) in pc_full_res.into_iter().chain(ext_full_res) {
            if let Some(h) = hash_opt {
                files[file_id].hash = Some(h);
            }
        }
    }

    // 5. 결과 분류 및 중복 그룹 계산
    {
        if let Ok(mut p) = progress_arc.lock() {
            p.stage = ScanStage::ClassifyingResults;
            p.percentage = 97.0;
            p.eta_seconds = Some(0);
            p.elapsed_seconds = start_instant.elapsed().as_secs_f64();
        }
    }

    let mut hash_groups: HashMap<String, Vec<usize>> = HashMap::new();
    for file in &files {
        if let Some(ref h) = file.hash {
            hash_groups.entry(h.clone()).or_default().push(file.id);
        }
    }

    let mut ext_duplicate_groups = Vec::new();
    let mut pc_duplicate_groups = Vec::new();
    let mut cross_duplicate_groups = Vec::new();

    for (hash, group_ids) in &hash_groups {
        if group_ids.len() < 2 {
            continue;
        }

        let mut pc_ids = Vec::new();
        let mut ext_ids = Vec::new();

        for &id in group_ids {
            match files[id].location {
                LocationKind::Pc => pc_ids.push(id),
                LocationKind::External => ext_ids.push(id),
            }
        }

        let pc_count = pc_ids.len();
        let ext_count = ext_ids.len();

        for &id in group_ids {
            files[id].pc_copy_count = pc_count;
            files[id].ext_copy_count = ext_count;
            files[id].matching_ids = group_ids
                .iter()
                .copied()
                .filter(|&other_id| other_id != id)
                .collect();

            if pc_count > 0 && ext_count > 0 {
                files[id].status = BackupStatus::BackedUp;
            }
        }

        let first_id = group_ids[0];
        let file_size = files[first_id].size;
        let category = files[first_id].category;
        let representative_name = files[first_id].name.clone();

        if ext_count >= 2 {
            let wasted_ext_bytes = (ext_count as u64 - 1) * file_size;
            ext_duplicate_groups.push(DuplicateGroup {
                hash: hash.clone(),
                file_size,
                category,
                representative_name: representative_name.clone(),
                pc_files: pc_ids.clone(),
                ext_files: ext_ids.clone(),
                wasted_ext_bytes,
            });
        }

        if pc_count >= 2 {
            let wasted_pc_bytes = (pc_count as u64 - 1) * file_size;
            pc_duplicate_groups.push(DuplicateGroup {
                hash: hash.clone(),
                file_size,
                category,
                representative_name: representative_name.clone(),
                pc_files: pc_ids.clone(),
                ext_files: ext_ids.clone(),
                wasted_ext_bytes: wasted_pc_bytes,
            });
        }

        if pc_count >= 1 && ext_count >= 1 {
            cross_duplicate_groups.push(DuplicateGroup {
                hash: hash.clone(),
                file_size,
                category,
                representative_name,
                pc_files: pc_ids,
                ext_files: ext_ids,
                wasted_ext_bytes: if ext_count >= 2 {
                    (ext_count as u64 - 1) * file_size
                } else {
                    0
                },
            });
        }
    }

    ext_duplicate_groups.sort_by(|a, b| b.wasted_ext_bytes.cmp(&a.wasted_ext_bytes));
    pc_duplicate_groups.sort_by(|a, b| b.wasted_ext_bytes.cmp(&a.wasted_ext_bytes));
    cross_duplicate_groups.sort_by(|a, b| b.file_size.cmp(&a.file_size));

    let mut unbacked_pc_ids = Vec::new();
    let mut backed_up_pc_ids = Vec::new();
    let mut ext_only_ids = Vec::new();
    let mut pc_folders_set = std::collections::BTreeSet::new();
    let mut ext_folders_set = std::collections::BTreeSet::new();

    let mut summary = ScanSummary {
        elapsed_secs: start_instant.elapsed().as_secs_f64(),
        ..Default::default()
    };

    for file in &files {
        match file.location {
            LocationKind::Pc => {
                summary.total_pc_files += 1;
                summary.total_pc_bytes += file.size;
                pc_folders_set.insert(file.relative_folder.clone());
                match file.status {
                    BackupStatus::UnbackedOnPc => {
                        summary.unbacked_pc_files += 1;
                        summary.unbacked_pc_bytes += file.size;
                        unbacked_pc_ids.push(file.id);
                    }
                    BackupStatus::BackedUp => {
                        summary.backed_up_pc_files += 1;
                        summary.backed_up_pc_bytes += file.size;
                        backed_up_pc_ids.push(file.id);
                    }
                    BackupStatus::ExternalOnly => {}
                }
            }
            LocationKind::External => {
                summary.total_ext_files += 1;
                summary.total_ext_bytes += file.size;
                ext_folders_set.insert(file.relative_folder.clone());
                if file.status == BackupStatus::ExternalOnly {
                    summary.ext_only_files += 1;
                    summary.ext_only_bytes += file.size;
                    ext_only_ids.push(file.id);
                }
            }
        }
    }

    summary.ext_duplicate_groups = ext_duplicate_groups.len();
    summary.ext_duplicate_extra_files = ext_duplicate_groups
        .iter()
        .map(|g| g.ext_files.len().saturating_sub(1))
        .sum();
    summary.ext_wasted_bytes = ext_duplicate_groups
        .iter()
        .map(|g| g.wasted_ext_bytes)
        .sum();

    summary.pc_duplicate_groups = pc_duplicate_groups.len();
    summary.pc_duplicate_extra_files = pc_duplicate_groups
        .iter()
        .map(|g| g.pc_files.len().saturating_sub(1))
        .sum();
    summary.pc_wasted_bytes = pc_duplicate_groups
        .iter()
        .map(|g| g.wasted_ext_bytes)
        .sum();

    summary.cross_duplicate_groups = cross_duplicate_groups.len();

    {
        if let Ok(mut p) = progress_arc.lock() {
            p.stage = ScanStage::Completed;
            p.percentage = 100.0;
            p.eta_seconds = Some(0);
            p.elapsed_seconds = summary.elapsed_secs;
        }
    }

    Some(ScanResult {
        files,
        unbacked_pc_ids,
        backed_up_pc_ids,
        ext_only_ids,
        ext_duplicate_groups,
        pc_duplicate_groups,
        cross_duplicate_groups,
        summary,
        pc_folders: pc_folders_set.into_iter().collect(),
        ext_folders: ext_folders_set.into_iter().collect(),
    })
}
