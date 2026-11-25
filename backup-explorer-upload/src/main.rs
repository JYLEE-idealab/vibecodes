#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod cleanup;
mod scanner;

use std::collections::HashSet;
use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;

use eframe::egui::{
    self, Align, Color32, ColorImage, CursorIcon, FontData, FontDefinitions, FontFamily, Frame,
    Key, Layout, Margin, RichText, Rounding, ScrollArea, Stroke, TextureHandle, TextureOptions,
    Vec2,
};
use egui_extras::{Column, TableBuilder};

use cleanup::{
    apply_cleanup_report_to_result, apply_smart_selection, can_select_file_safely,
    is_copy_filename_pattern, run_cleanup_background, select_duplicates_in_subfolder,
    CleanupMethod, CleanupProgress, SmartSelectRule, TargetScope,
};
use scanner::{
    format_duration, format_size, run_scan, BackupStatus, FileCategory, LocationKind, ScanOptions,
    ScanProgress, ScanResult, ScanStage,
};

// Windows 10/11 Modern Explorer 컬러 팔레트
const BG_RIBBON: Color32 = Color32::from_rgb(243, 244, 246);
const BG_SIDEBAR: Color32 = Color32::from_rgb(249, 250, 251);
const BG_SURFACE: Color32 = Color32::from_rgb(255, 255, 255);
const BG_SUBTLE: Color32 = Color32::from_rgb(245, 247, 250);
const BORDER_LIGHT: Color32 = Color32::from_rgb(226, 229, 234);
const ACCENT_BLUE: Color32 = Color32::from_rgb(0, 103, 192);
const ACCENT_BLUE_BG: Color32 = Color32::from_rgb(229, 241, 251);
const TEXT_PRIMARY: Color32 = Color32::from_rgb(28, 30, 33);
const TEXT_MUTED: Color32 = Color32::from_rgb(107, 114, 128);
const COLOR_SUCCESS: Color32 = Color32::from_rgb(15, 123, 15);
const COLOR_WARN: Color32 = Color32::from_rgb(196, 81, 0);
const COLOR_DANGER: Color32 = Color32::from_rgb(196, 43, 28);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NavTab {
    Dashboard,
    UnbackedPc,
    BackedUpPc,
    ExtDuplicates,
    CrossDuplicates,
    PcDuplicates,
    ExtOnly,
}

impl NavTab {
    fn short_title(&self) -> &'static str {
        match self {
            NavTab::Dashboard => "요약",
            NavTab::UnbackedPc => "미백업",
            NavTab::BackedUpPc => "백업 완료",
            NavTab::ExtDuplicates => "외장 중복",
            NavTab::CrossDuplicates => "PC·외장 동일",
            NavTab::PcDuplicates => "PC 중복",
            NavTab::ExtOnly => "외장 전용",
        }
    }

    fn tooltip(&self) -> &'static str {
        match self {
            NavTab::Dashboard => "PC와 외장하드의 전체 백업 현황 및 중복으로 낭비 중인 용량을 한눈에 요약합니다.",
            NavTab::UnbackedPc => "외장하드 어느 폴더에도 백업되어 있지 않은 내 PC 파일입니다. (폴더 구조·이름 무관 내용 기준)",
            NavTab::BackedUpPc => "폴더 위치나 파일명이 달라도 외장하드 어딘가에 동일한 내용으로 백업되어 있는 안전한 파일입니다.",
            NavTab::ExtDuplicates => "외장하드 내부의 여러 폴더에 2개 이상 중복 저장되어 용량을 차지하고 있는 파일 그룹입니다.",
            NavTab::CrossDuplicates => "내 PC와 외장하드 양쪽에 모두 존재하는 동일 파일 그룹과 각각의 저장 위치를 비교합니다.",
            NavTab::PcDuplicates => "내 PC(M.2 SSD) 내부에 2개 이상 중복 저장되어 있는 파일 그룹입니다.",
            NavTab::ExtOnly => "내 PC에는 없고 외장하드에만 보관되어 있는 파일입니다.",
        }
    }
}

fn category_short_label(cat: FileCategory) -> &'static str {
    match cat {
        FileCategory::All => "전체",
        FileCategory::Photo => "사진",
        FileCategory::Video => "영상",
        FileCategory::Document => "문서",
        FileCategory::Audio => "오디오",
        FileCategory::Archive => "압축",
        FileCategory::Other => "기타",
    }
}

fn category_tooltip(cat: FileCategory) -> &'static str {
    match cat {
        FileCategory::All => "모든 확장자의 파일을 표시합니다.",
        FileCategory::Photo => "사진 및 이미지 파일 (JPG, PNG, HEIC, RAW, PSD, WEBP 등)",
        FileCategory::Video => "동영상 파일 (MP4, MKV, MOV, AVI, WMV, MTS 등)",
        FileCategory::Document => "문서 파일 (PDF, HWP, DOCX, XLSX, PPTX, TXT 등)",
        FileCategory::Audio => "음악 및 오디오 파일 (MP3, WAV, FLAC, M4A 등)",
        FileCategory::Archive => "압축 및 디스크 이미지 (ZIP, RAR, 7Z, ISO 등)",
        FileCategory::Other => "그 외 모든 일반 파일",
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SortColumn {
    Name,
    Size,
    Extension,
    Modified,
    Path,
    Copies,
}

struct RawDecodedThumbnail {
    path: PathBuf,
    rgba_bytes: Vec<u8>,
    thumb_w: usize,
    thumb_h: usize,
    orig_w: u32,
    orig_h: u32,
    error: Option<String>,
}

struct ImagePreviewCache {
    path: PathBuf,
    is_loading: bool,
    texture: Option<TextureHandle>,
    dimensions: Option<(u32, u32)>,
    error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct TableCacheKey {
    result_version: usize,
    nav_tab: NavTab,
    category_filter: FileCategory,
    custom_ext_filter: String,
    search_query: String,
    subfolder_filter: Option<String>,
    sort_col: SortColumn,
    sort_asc: bool,
}

struct BackupExplorerApp {
    pc_roots: Vec<PathBuf>,
    ext_roots: Vec<PathBuf>,
    scan_options: ScanOptions,

    nav_tab: NavTab,
    category_filter: FileCategory,
    custom_ext_filter: String,
    search_query: String,
    subfolder_filter: Option<String>,

    sort_col: SortColumn,
    sort_asc: bool,

    is_scanning: bool,
    progress: Arc<Mutex<ScanProgress>>,
    cancel_flag: Arc<AtomicBool>,
    scan_result_slot: Arc<Mutex<Option<ScanResult>>>,
    result: Option<ScanResult>,
    result_version: usize,

    table_cache_key: Option<TableCacheKey>,
    cached_filtered_ids: Vec<usize>,
    cached_filtered_bytes: u64,

    selected_file_id: Option<usize>,
    selected_dup_hash: Option<String>,
    status_toast: Option<String>,

    marked_for_cleanup: HashSet<usize>,
    only_show_marked_groups: bool,
    cleanup_method: CleanupMethod,
    quarantine_root: PathBuf,
    show_cleanup_confirm_modal: bool,
    cleanup_progress: Arc<Mutex<CleanupProgress>>,
    last_quarantine_used: Option<PathBuf>,

    image_preview: Option<ImagePreviewCache>,
    decoded_thumb_slot: Arc<Mutex<Option<RawDecodedThumbnail>>>,
}

impl Default for BackupExplorerApp {
    fn default() -> Self {
        Self {
            pc_roots: Vec::new(),
            ext_roots: Vec::new(),
            scan_options: ScanOptions::default(),
            nav_tab: NavTab::Dashboard,
            category_filter: FileCategory::All,
            custom_ext_filter: String::new(),
            search_query: String::new(),
            subfolder_filter: None,
            sort_col: SortColumn::Size,
            sort_asc: false,
            is_scanning: false,
            progress: Arc::new(Mutex::new(ScanProgress::default())),
            cancel_flag: Arc::new(AtomicBool::new(false)),
            scan_result_slot: Arc::new(Mutex::new(None)),
            result: None,
            result_version: 0,
            table_cache_key: None,
            cached_filtered_ids: Vec::new(),
            cached_filtered_bytes: 0,
            selected_file_id: None,
            selected_dup_hash: None,
            status_toast: None,
            marked_for_cleanup: HashSet::new(),
            only_show_marked_groups: false,
            cleanup_method: CleanupMethod::MoveToQuarantine,
            quarantine_root: PathBuf::from("D:\\_중복격리보관함"),
            show_cleanup_confirm_modal: false,
            cleanup_progress: Arc::new(Mutex::new(CleanupProgress::default())),
            last_quarantine_used: None,
            image_preview: None,
            decoded_thumb_slot: Arc::new(Mutex::new(None)),
        }
    }
}

fn setup_modern_korean_font(ctx: &egui::Context) {
    let mut fonts = FontDefinitions::default();
    // Noto Sans KR을 최우선으로 로드하여 깔끔하고 세련된 가독성 확보
    let candidates = [
        "C:\\Windows\\Fonts\\NotoSansKR-Regular.ttf",
        "C:\\Windows\\Fonts\\malgunsl.ttf",
        "C:\\Windows\\Fonts\\malgun.ttf",
        "C:\\Windows\\Fonts\\Gulim.ttc",
    ];

    for path in candidates {
        if let Ok(bytes) = std::fs::read(path) {
            fonts
                .font_data
                .insert("korean".to_owned(), FontData::from_owned(bytes));
            fonts
                .families
                .entry(FontFamily::Proportional)
                .or_default()
                .insert(0, "korean".to_owned());
            fonts
                .families
                .entry(FontFamily::Monospace)
                .or_default()
                .insert(0, "korean".to_owned());
            break;
        }
    }

    ctx.set_fonts(fonts);
}

fn configure_win_explorer_theme(ctx: &egui::Context) {
    let mut visuals = egui::Visuals::light();
    visuals.window_fill = BG_SURFACE;
    visuals.panel_fill = BG_SIDEBAR;
    visuals.faint_bg_color = Color32::from_rgb(249, 250, 252);
    visuals.extreme_bg_color = BG_SURFACE;

    visuals.selection.bg_fill = ACCENT_BLUE_BG;
    visuals.selection.stroke = Stroke::new(1.0_f32, ACCENT_BLUE);

    visuals.widgets.noninteractive.bg_stroke = Stroke::new(1.0_f32, BORDER_LIGHT);
    visuals.widgets.noninteractive.fg_stroke = Stroke::new(1.0_f32, TEXT_PRIMARY);

    visuals.widgets.inactive.weak_bg_fill = BG_SURFACE;
    visuals.widgets.inactive.bg_fill = BG_SURFACE;
    visuals.widgets.inactive.bg_stroke = Stroke::new(1.0_f32, BORDER_LIGHT);
    visuals.widgets.inactive.rounding = Rounding::same(4.0);

    visuals.widgets.hovered.weak_bg_fill = Color32::from_rgb(236, 240, 245);
    visuals.widgets.hovered.bg_fill = Color32::from_rgb(236, 240, 245);
    visuals.widgets.hovered.bg_stroke = Stroke::new(1.0_f32, Color32::from_rgb(190, 200, 215));
    visuals.widgets.hovered.rounding = Rounding::same(4.0);

    visuals.widgets.active.bg_fill = ACCENT_BLUE_BG;
    visuals.widgets.active.bg_stroke = Stroke::new(1.0_f32, ACCENT_BLUE);
    visuals.widgets.active.rounding = Rounding::same(4.0);

    visuals.window_rounding = Rounding::same(6.0);
    ctx.set_visuals(visuals);

    ctx.style_mut(|style| {
        style.interaction.selectable_labels = false;
        style.interaction.tooltip_delay = 0.25;
        style.spacing.item_spacing = Vec2::new(6.0, 5.0);
        style.spacing.button_padding = Vec2::new(8.0, 4.0);
    });
}

#[cfg(target_os = "windows")]
fn open_in_windows_explorer(path: &Path) {
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::process::CommandExt;

    #[link(name = "shell32")]
    extern "system" {
        fn ILCreateFromPathW(pszPath: *const u16) -> *mut std::ffi::c_void;
        fn ILFree(pidl: *mut std::ffi::c_void);
        fn SHOpenFolderAndSelectItems(
            pidlFolder: *const std::ffi::c_void,
            cidl: u32,
            apidl: *const *const std::ffi::c_void,
            dwFlags: u32,
        ) -> i32;
    }

    #[link(name = "ole32")]
    extern "system" {
        fn CoInitializeEx(pvReserved: *mut std::ffi::c_void, dwCoInit: u32) -> i32;
    }

    let raw_str = path.to_string_lossy().replace('/', "\\");
    let clean_str = raw_str.strip_prefix(r"\\?\").unwrap_or(&raw_str);
    let clean_path = Path::new(clean_str);

    if clean_path.is_dir() {
        let _ = Command::new("explorer.exe").arg(clean_path).spawn();
        return;
    }

    let wide: Vec<u16> = clean_path
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();

    unsafe {
        let _ = CoInitializeEx(std::ptr::null_mut(), 0x2);
        let pidl = ILCreateFromPathW(wide.as_ptr());
        if !pidl.is_null() {
            let hr = SHOpenFolderAndSelectItems(pidl, 0, std::ptr::null(), 0);
            ILFree(pidl);
            if hr >= 0 {
                return;
            }
        }
    }

    if clean_path.exists() {
        let _ = Command::new("explorer.exe")
            .raw_arg(format!("/select,\"{}\"", clean_str))
            .spawn();
    } else if let Some(parent) = clean_path.parent() {
        let _ = Command::new("explorer.exe").arg(parent).spawn();
    }
}

#[cfg(target_os = "windows")]
fn open_file_directly(path: &Path) {
    use std::os::windows::process::CommandExt;
    let raw_str = path.to_string_lossy().replace('/', "\\");
    let clean_str = raw_str.strip_prefix(r"\\?\").unwrap_or(&raw_str);
    let _ = Command::new("cmd.exe")
        .raw_arg(format!("/C start \"\" \"{}\"", clean_str))
        .creation_flags(0x08000000)
        .spawn();
}

fn is_previewable_image(ext: &str) -> bool {
    matches!(
        ext.to_ascii_lowercase().as_str(),
        "jpg" | "jpeg" | "png" | "gif" | "bmp" | "webp" | "ico" | "tif" | "tiff" | "jfif"
    )
}

fn short_folder_chip(path: &Path) -> String {
    let s = path.to_string_lossy();
    if s.len() <= 22 {
        return s.to_string();
    }
    if let Some(name) = path.file_name() {
        format!("…\\{}", name.to_string_lossy())
    } else {
        s.to_string()
    }
}

impl BackupExplorerApp {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        setup_modern_korean_font(&cc.egui_ctx);
        configure_win_explorer_theme(&cc.egui_ctx);
        Self::default()
    }

    fn auto_detect_external_drives(&mut self) {
        for letter in ['D', 'E', 'F', 'H', 'I', 'J', 'K', 'L', 'M'] {
            let root = PathBuf::from(format!("{}:\\", letter));
            if root.exists() && !self.ext_roots.contains(&root) {
                self.ext_roots.push(root);
            }
        }
        self.update_default_quarantine_root();
    }

    fn request_async_image_preview(&mut self, path: PathBuf) {
        let already_requested = match &self.image_preview {
            Some(p) => p.path == path,
            None => false,
        };
        if already_requested {
            return;
        }

        self.image_preview = Some(ImagePreviewCache {
            path: path.clone(),
            is_loading: true,
            texture: None,
            dimensions: None,
            error: None,
        });

        let slot = Arc::clone(&self.decoded_thumb_slot);
        thread::spawn(move || {
            let decoded = match image::ImageReader::open(&path)
                .and_then(|r| r.with_guessed_format())
                .map_err(|e| e.to_string())
                .and_then(|r| r.decode().map_err(|e| e.to_string()))
            {
                Ok(img) => {
                    let orig_w = img.width();
                    let orig_h = img.height();
                    let thumb = img.thumbnail(360, 260);
                    let rgba = thumb.to_rgba8();
                    let w = rgba.width() as usize;
                    let h = rgba.height() as usize;
                    RawDecodedThumbnail {
                        path,
                        rgba_bytes: rgba.into_raw(),
                        thumb_w: w,
                        thumb_h: h,
                        orig_w,
                        orig_h,
                        error: None,
                    }
                }
                Err(e) => RawDecodedThumbnail {
                    path,
                    rgba_bytes: Vec::new(),
                    thumb_w: 0,
                    thumb_h: 0,
                    orig_w: 0,
                    orig_h: 0,
                    error: Some(format!("미리보기 불가: {}", e)),
                },
            };

            if let Ok(mut guard) = slot.lock() {
                *guard = Some(decoded);
            }
        });
    }

    fn poll_async_image_preview(&mut self, ctx: &egui::Context) {
        let maybe_raw = self
            .decoded_thumb_slot
            .lock()
            .ok()
            .and_then(|mut g| g.take());
        if let Some(raw) = maybe_raw {
            if let Some(ref mut current) = self.image_preview {
                if current.path == raw.path {
                    current.is_loading = false;
                    if let Some(err) = raw.error {
                        current.error = Some(err);
                    } else if raw.thumb_w > 0 && raw.thumb_h > 0 {
                        let color_img = ColorImage::from_rgba_unmultiplied(
                            [raw.thumb_w, raw.thumb_h],
                            &raw.rgba_bytes,
                        );
                        let tex = ctx.load_texture(
                            raw.path.to_string_lossy().to_string(),
                            color_img,
                            TextureOptions::LINEAR,
                        );
                        current.texture = Some(tex);
                        current.dimensions = Some((raw.orig_w, raw.orig_h));
                    }
                }
            }
        }
    }

    fn add_default_user_folders(&mut self) {
        if let Ok(user_profile) = std::env::var("USERPROFILE") {
            let base = PathBuf::from(user_profile);
            for sub in ["Pictures", "Videos", "Documents", "Desktop", "Downloads"] {
                let p = base.join(sub);
                if p.exists() && !self.pc_roots.contains(&p) {
                    self.pc_roots.push(p);
                }
            }
        }
    }

    fn update_default_quarantine_root(&mut self) {
        if let Some(first_ext) = self.ext_roots.first() {
            let mut root = PathBuf::new();
            if let Some(prefix) = first_ext.components().next() {
                root.push(prefix.as_os_str());
                root.push("\\");
                root.push("_중복격리보관함");
                self.quarantine_root = root;
            }
        }
    }

    fn start_scan(&mut self) {
        if self.pc_roots.is_empty() && self.ext_roots.is_empty() {
            self.status_toast = Some("비교할 내 PC 또는 외장하드 폴더를 먼저 추가해 주세요.".to_string());
            return;
        }

        self.update_default_quarantine_root();
        self.is_scanning = true;
        self.cancel_flag.store(false, Ordering::Relaxed);
        self.selected_file_id = None;
        self.selected_dup_hash = None;
        self.subfolder_filter = None;
        self.status_toast = None;
        self.marked_for_cleanup.clear();
        self.only_show_marked_groups = false;
        self.image_preview = None;
        self.table_cache_key = None;

        if let Ok(mut slot) = self.scan_result_slot.lock() {
            *slot = None;
        }

        let pc_roots = self.pc_roots.clone();
        let ext_roots = self.ext_roots.clone();
        let options = self.scan_options;
        let progress_arc = Arc::clone(&self.progress);
        let cancel_flag = Arc::clone(&self.cancel_flag);
        let result_slot = Arc::clone(&self.scan_result_slot);

        thread::spawn(move || {
            let res = run_scan(pc_roots, ext_roots, options, progress_arc, cancel_flag);
            if let Ok(mut slot) = result_slot.lock() {
                *slot = res;
            }
        });
    }

    fn start_background_cleanup(&mut self) {
        let Some(ref res) = self.result else {
            return;
        };

        let mut sorted_ids: Vec<usize> = self.marked_for_cleanup.iter().copied().collect();
        sorted_ids.sort_unstable();

        let mut safe_targets: Vec<(usize, PathBuf, u64, String)> = Vec::new();
        let mut simulated_removed: HashSet<usize> = HashSet::new();

        for file_id in sorted_ids {
            if !can_select_file_safely(file_id, &res.files, &simulated_removed) {
                continue;
            }
            let Some(entry) = res.files.get(file_id) else {
                continue;
            };
            simulated_removed.insert(file_id);
            safe_targets.push((
                file_id,
                entry.path.clone(),
                entry.size,
                entry.name.clone(),
            ));
        }

        let total = safe_targets.len();
        if let Ok(mut p) = self.cleanup_progress.lock() {
            *p = CleanupProgress {
                is_running: true,
                processed: 0,
                total,
                current_file: String::new(),
                finished_report: None,
            };
        }

        let method = self.cleanup_method;
        let q_root = self.quarantine_root.clone();
        let prog_arc = Arc::clone(&self.cleanup_progress);

        thread::spawn(move || {
            run_cleanup_background(safe_targets, method, q_root, prog_arc);
        });
    }

    fn matches_filters(&self, entry: &scanner::FileEntry) -> bool {
        if self.category_filter != FileCategory::All && entry.category != self.category_filter {
            return false;
        }

        let custom_trim = self.custom_ext_filter.trim();
        if !custom_trim.is_empty() {
            let entry_ext = entry.extension.to_ascii_lowercase();
            let matched = custom_trim
                .split(|c: char| c == ',' || c == ';' || c.is_whitespace())
                .map(|s| s.trim().trim_start_matches('.').to_ascii_lowercase())
                .filter(|s| !s.is_empty())
                .any(|ext| ext == entry_ext);
            if !matched {
                return false;
            }
        }

        if let Some(ref sub) = self.subfolder_filter {
            if &entry.relative_folder != sub {
                return false;
            }
        }

        let q = self.search_query.trim();
        if !q.is_empty() {
            let q_lower = q.to_lowercase();
            let name_match = entry.name.to_lowercase().contains(&q_lower);
            let path_match = entry
                .path
                .to_string_lossy()
                .to_lowercase()
                .contains(&q_lower);
            if !name_match && !path_match {
                return false;
            }
        }

        true
    }

    fn marked_total_bytes(&self) -> u64 {
        let Some(ref res) = self.result else {
            return 0;
        };
        self.marked_for_cleanup
            .iter()
            .filter_map(|&id| res.files.get(id))
            .map(|f| f.size)
            .sum()
    }

    fn toggle_file_cleanup_mark(&mut self, file_id: usize) {
        if self.marked_for_cleanup.contains(&file_id) {
            self.marked_for_cleanup.remove(&file_id);
        } else if let Some(ref res) = self.result {
            if can_select_file_safely(file_id, &res.files, &self.marked_for_cleanup) {
                self.marked_for_cleanup.insert(file_id);
            } else {
                self.status_toast =
                    Some("최소 1개의 원본 파일은 반드시 보존되어야 합니다.".to_string());
            }
        }
    }

    fn export_current_view_csv(&mut self) {
        let Some(ref res) = self.result else {
            return;
        };

        let Some(save_path) = rfd::FileDialog::new()
            .set_title("분석 결과 CSV 저장")
            .set_file_name("백업_중복_분석결과.csv")
            .add_filter("CSV 파일 (*.csv)", &["csv"])
            .save_file()
        else {
            return;
        };

        let mut file = match File::create(&save_path) {
            Ok(f) => f,
            Err(e) => {
                self.status_toast = Some(format!("저장 실패: {}", e));
                return;
            }
        };

        let _ = file.write_all(&[0xEF, 0xBB, 0xBF]);
        let _ = writeln!(
            file,
            "위치,상태,파일명,확장자,분류,크기(Bytes),크기,수정일,내PC수,외장수,경로,동일파일경로,해시"
        );

        for entry in &res.files {
            if !self.matches_filters(entry) {
                continue;
            }
            let other_paths: Vec<String> = entry
                .matching_ids
                .iter()
                .filter_map(|&id| res.files.get(id))
                .map(|other| {
                    format!(
                        "[{}] {}",
                        other.location.label(),
                        other.path.to_string_lossy()
                    )
                })
                .collect();

            let _ = writeln!(
                file,
                "\"{}\",\"{}\",\"{}\",\"{}\",\"{}\",{},\"{}\",\"{}\",{},{},\"{}\",\"{}\",\"{}\"",
                entry.location.label(),
                entry.status.label(),
                entry.name.replace('"', "\"\""),
                entry.extension,
                entry.category.label(),
                entry.size,
                format_size(entry.size),
                entry.modified_str,
                entry.pc_copy_count,
                entry.ext_copy_count,
                entry.path.to_string_lossy().replace('"', "\"\""),
                other_paths.join(" | ").replace('"', "\"\""),
                entry.hash.as_deref().unwrap_or("-")
            );
        }

        self.status_toast = Some(format!("CSV 저장 완료: {}", save_path.to_string_lossy()));
    }

    fn render_ribbon_top(&mut self, ui: &mut egui::Ui) {
        Frame::none()
            .fill(BG_RIBBON)
            .inner_margin(Margin::symmetric(12.0, 8.0))
            .show(ui, |ui| {
                // 1단: 경로 지정 바 + 옵션 + 분석 시작
                ui.horizontal(|ui| {
                    // 내 PC 경로 바
                    Frame::none()
                        .fill(BG_SURFACE)
                        .stroke(Stroke::new(1.0_f32, BORDER_LIGHT))
                        .rounding(Rounding::same(4.0))
                        .inner_margin(Margin::symmetric(8.0, 4.0))
                        .show(ui, |ui| {
                            ui.set_min_width(350.0);
                            ui.horizontal(|ui| {
                                ui.label(RichText::new("내 PC").strong().color(ACCENT_BLUE))
                                    .on_hover_text("비교할 내 PC(M.2 SSD)의 폴더 목록입니다.");

                                if ui
                                    .add_enabled(!self.is_scanning, egui::Button::new("+ 추가").small())
                                    .on_hover_text("내 PC 폴더 선택")
                                    .on_hover_cursor(CursorIcon::PointingHand)
                                    .clicked()
                                {
                                    if let Some(folder) = rfd::FileDialog::new()
                                        .set_title("내 PC 폴더 선택")
                                        .pick_folder()
                                    {
                                        if !self.pc_roots.contains(&folder) {
                                            self.pc_roots.push(folder);
                                        }
                                    }
                                }

                                if ui
                                    .add_enabled(!self.is_scanning, egui::Button::new("자동").small())
                                    .on_hover_text("내 주요 폴더 자동 추가 (사진, 동영상, 문서, 바탕화면, 다운로드)")
                                    .on_hover_cursor(CursorIcon::PointingHand)
                                    .clicked()
                                {
                                    self.add_default_user_folders();
                                }

                                if !self.pc_roots.is_empty()
                                    && ui
                                        .add_enabled(
                                            !self.is_scanning,
                                            egui::Button::new("비우기").small(),
                                        )
                                        .on_hover_text("내 PC 폴더 목록 비우기")
                                        .clicked()
                                {
                                    self.pc_roots.clear();
                                }

                                ui.separator();

                                if self.pc_roots.is_empty() {
                                    ui.label(
                                        RichText::new("폴더를 추가하세요")
                                            .small()
                                            .color(TEXT_MUTED),
                                    );
                                } else {
                                    let mut remove_idx = None;
                                    ScrollArea::horizontal()
                                        .id_salt("pc_chips_scroll")
                                        .max_width(210.0)
                                        .show(ui, |ui| {
                                            ui.horizontal(|ui| {
                                                for (idx, path) in self.pc_roots.iter().enumerate()
                                                {
                                                    let chip_txt =
                                                        format!("{} ×", short_folder_chip(path));
                                                    let resp = ui
                                                        .add_enabled(
                                                            !self.is_scanning,
                                                            egui::Button::new(
                                                                RichText::new(chip_txt).small(),
                                                            )
                                                            .fill(ACCENT_BLUE_BG)
                                                            .stroke(Stroke::NONE)
                                                            .rounding(Rounding::same(10.0)),
                                                        )
                                                        .on_hover_text(format!(
                                                            "{}\n(클릭 시 제거)",
                                                            path.to_string_lossy()
                                                        ));
                                                    if resp.clicked() {
                                                        remove_idx = Some(idx);
                                                    }
                                                }
                                            });
                                        });
                                    if let Some(idx) = remove_idx {
                                        self.pc_roots.remove(idx);
                                    }
                                }
                            });
                        });

                    // 외장하드 경로 바
                    Frame::none()
                        .fill(BG_SURFACE)
                        .stroke(Stroke::new(1.0_f32, BORDER_LIGHT))
                        .rounding(Rounding::same(4.0))
                        .inner_margin(Margin::symmetric(8.0, 4.0))
                        .show(ui, |ui| {
                            ui.set_min_width(350.0);
                            ui.horizontal(|ui| {
                                ui.label(RichText::new("외장하드").strong().color(COLOR_SUCCESS))
                                    .on_hover_text("비교할 외장하드 드라이브(예: D:\\) 또는 특정 백업 폴더입니다.");

                                if ui
                                    .add_enabled(!self.is_scanning, egui::Button::new("+ 추가").small())
                                    .on_hover_text("외장하드 드라이브 또는 폴더 선택")
                                    .on_hover_cursor(CursorIcon::PointingHand)
                                    .clicked()
                                {
                                    if let Some(folder) = rfd::FileDialog::new()
                                        .set_title("외장하드 폴더 선택")
                                        .pick_folder()
                                    {
                                        if !self.ext_roots.contains(&folder) {
                                            self.ext_roots.push(folder);
                                            self.update_default_quarantine_root();
                                        }
                                    }
                                }

                                if ui
                                    .add_enabled(!self.is_scanning, egui::Button::new("감지").small())
                                    .on_hover_text("연결된 외장 드라이브 자동 감지")
                                    .on_hover_cursor(CursorIcon::PointingHand)
                                    .clicked()
                                {
                                    self.auto_detect_external_drives();
                                }

                                if !self.ext_roots.is_empty()
                                    && ui
                                        .add_enabled(
                                            !self.is_scanning,
                                            egui::Button::new("비우기").small(),
                                        )
                                        .on_hover_text("외장하드 폴더 목록 비우기")
                                        .clicked()
                                {
                                    self.ext_roots.clear();
                                }

                                ui.separator();

                                if self.ext_roots.is_empty() {
                                    ui.label(
                                        RichText::new("외장하드 선택")
                                            .small()
                                            .color(TEXT_MUTED),
                                    );
                                } else {
                                    let mut remove_idx = None;
                                    ScrollArea::horizontal()
                                        .id_salt("ext_chips_scroll")
                                        .max_width(210.0)
                                        .show(ui, |ui| {
                                            ui.horizontal(|ui| {
                                                for (idx, path) in self.ext_roots.iter().enumerate()
                                                {
                                                    let chip_txt =
                                                        format!("{} ×", short_folder_chip(path));
                                                    let resp = ui
                                                        .add_enabled(
                                                            !self.is_scanning,
                                                            egui::Button::new(
                                                                RichText::new(chip_txt).small(),
                                                            )
                                                            .fill(Color32::from_rgb(232, 245, 233))
                                                            .stroke(Stroke::NONE)
                                                            .rounding(Rounding::same(10.0)),
                                                        )
                                                        .on_hover_text(format!(
                                                            "{}\n(클릭 시 제거)",
                                                            path.to_string_lossy()
                                                        ));
                                                    if resp.clicked() {
                                                        remove_idx = Some(idx);
                                                    }
                                                }
                                            });
                                        });
                                    if let Some(idx) = remove_idx {
                                        self.ext_roots.remove(idx);
                                    }
                                }
                            });
                        });

                    // 우측 끝: 분석 시작 버튼 + 옵션 드롭다운
                    ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                        if !self.is_scanning {
                            let start_btn = egui::Button::new(
                                RichText::new("분석 시작")
                                    .strong()
                                    .color(Color32::WHITE),
                            )
                            .fill(ACCENT_BLUE)
                            .stroke(Stroke::NONE)
                            .min_size(Vec2::new(92.0, 28.0));

                            if ui
                                .add(start_btn)
                                .on_hover_text("지정한 내 PC와 외장하드 폴더를 비교 분석합니다. (단축키: F5)")
                                .on_hover_cursor(CursorIcon::PointingHand)
                                .clicked()
                            {
                                self.start_scan();
                            }
                        } else {
                            let stop_btn = egui::Button::new(
                                RichText::new("중지")
                                    .strong()
                                    .color(Color32::WHITE),
                            )
                            .fill(COLOR_DANGER)
                            .stroke(Stroke::NONE)
                            .min_size(Vec2::new(76.0, 28.0));

                            if ui
                                .add(stop_btn)
                                .on_hover_text("진행 중인 분석을 즉시 중단합니다.")
                                .on_hover_cursor(CursorIcon::PointingHand)
                                .clicked()
                            {
                                self.cancel_flag.store(true, Ordering::Relaxed);
                            }
                        }

                        ui.menu_button("옵션", |ui| {
                            ui.set_min_width(210.0);
                            ui.add_enabled(
                                !self.is_scanning,
                                egui::Checkbox::new(
                                    &mut self.scan_options.fast_sample_mode,
                                    "초고속 샘플 해시 모드",
                                ),
                            )
                            .on_hover_text("파일 크기 + 앞/중간/뒤 48KB 해시로 비교해 대용량 영상도 초고속으로 판별합니다.\n해제 시 파일 전체를 100% 끝까지 읽어 대조합니다.");

                            ui.add_enabled(
                                !self.is_scanning,
                                egui::Checkbox::new(
                                    &mut self.scan_options.skip_system_folders,
                                    "시스템·캐시 폴더 제외",
                                ),
                            )
                            .on_hover_text("Windows, Program Files, AppData, node_modules 등 백업과 무관한 시스템/앱 캐시 폴더를 자동으로 건너뜁니다.");

                            ui.add_enabled(
                                !self.is_scanning,
                                egui::Checkbox::new(
                                    &mut self.scan_options.skip_zero_byte,
                                    "0바이트 빈 파일 제외",
                                ),
                            )
                            .on_hover_text("내용이 없는 0바이트 빈 파일을 분석에서 제외합니다.");
                        })
                        .response
                        .on_hover_text("비교 속도 및 제외 폴더 설정");
                    });
                });

                ui.add_space(6.0);

                // 2단: 유형 필터 칩 + 검색창 + 정리 실행 버튼
                ui.horizontal(|ui| {
                    for cat in [
                        FileCategory::All,
                        FileCategory::Photo,
                        FileCategory::Video,
                        FileCategory::Document,
                        FileCategory::Audio,
                        FileCategory::Archive,
                        FileCategory::Other,
                    ] {
                        let selected = self.category_filter == cat;
                        let btn = egui::Button::new(
                            RichText::new(category_short_label(cat))
                                .small()
                                .color(if selected { ACCENT_BLUE } else { TEXT_PRIMARY }),
                        )
                        .fill(if selected {
                            ACCENT_BLUE_BG
                        } else {
                            Color32::TRANSPARENT
                        })
                        .stroke(if selected {
                            Stroke::new(1.0_f32, ACCENT_BLUE)
                        } else {
                            Stroke::NONE
                        })
                        .rounding(Rounding::same(4.0));

                        if ui
                            .add(btn)
                            .on_hover_text(category_tooltip(cat))
                            .on_hover_cursor(CursorIcon::PointingHand)
                            .clicked()
                        {
                            self.category_filter = cat;
                        }
                    }

                    ui.separator();

                    ui.add(
                        egui::TextEdit::singleline(&mut self.custom_ext_filter)
                            .hint_text("확장자 (jpg, mp4)")
                            .desired_width(110.0),
                    )
                    .on_hover_text("쉼표(,)로 구분해 특정 확장자만 필터링합니다. 예: jpg, raw, mp4");
                    if !self.custom_ext_filter.is_empty() && ui.small_button("×").clicked() {
                        self.custom_ext_filter.clear();
                    }

                    ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                        if self.result.is_some()
                            && ui
                                .button("CSV 내보내기")
                                .on_hover_text("현재 필터링된 파일 목록을 엑셀용 CSV 파일로 저장합니다.")
                                .on_hover_cursor(CursorIcon::PointingHand)
                                .clicked()
                        {
                            self.export_current_view_csv();
                        }

                        if !self.search_query.is_empty() && ui.small_button("×").clicked() {
                            self.search_query.clear();
                        }
                        ui.add(
                            egui::TextEdit::singleline(&mut self.search_query)
                                .hint_text("검색...")
                                .desired_width(170.0),
                        )
                        .on_hover_text("파일 이름이나 폴더 경로로 실시간 검색합니다.");

                        let marked_cnt = self.marked_for_cleanup.len();
                        if marked_cnt > 0 {
                            ui.separator();
                            let marked_sz = self.marked_total_bytes();
                            let clean_btn = egui::Button::new(
                                RichText::new(format!(
                                    "정리 ({}개 · {})",
                                    marked_cnt,
                                    format_size(marked_sz)
                                ))
                                .strong()
                                .color(Color32::WHITE),
                            )
                            .fill(COLOR_WARN)
                            .stroke(Stroke::NONE);

                            if ui
                                .add(clean_btn)
                                .on_hover_text("선택된 중복 사본들을 검토한 뒤 격리 폴더 또는 휴지통으로 안전하게 이동합니다.")
                                .on_hover_cursor(CursorIcon::PointingHand)
                                .clicked()
                            {
                                self.show_cleanup_confirm_modal = true;
                            }
                        } else if let Some(ref q_dir) = self.last_quarantine_used {
                            if q_dir.exists()
                                && ui
                                    .button("격리함 열기")
                                    .on_hover_text(format!(
                                        "방금 이동한 격리 보관함 열기:\n{}",
                                        q_dir.to_string_lossy()
                                    ))
                                    .clicked()
                            {
                                open_in_windows_explorer(q_dir);
                            }
                        }
                    });
                });

                // 스캔 진행 배너
                if self.is_scanning {
                    ui.add_space(6.0);
                    let snap = self
                        .progress
                        .lock()
                        .map(|p| p.clone())
                        .unwrap_or_default();

                    Frame::none()
                        .fill(ACCENT_BLUE_BG)
                        .stroke(Stroke::new(1.0_f32, ACCENT_BLUE))
                        .rounding(Rounding::same(4.0))
                        .inner_margin(Margin::symmetric(10.0, 6.0))
                        .show(ui, |ui| {
                            ui.horizontal(|ui| {
                                ui.label(
                                    RichText::new(snap.stage.label())
                                        .strong()
                                        .small()
                                        .color(ACCENT_BLUE),
                                );
                                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                                    let eta_text = match snap.eta_seconds {
                                        Some(0) => "곧 완료".to_string(),
                                        Some(s) => format!("약 {} 남음", format_duration(s)),
                                        None => "계산 중...".to_string(),
                                    };
                                    ui.label(
                                        RichText::new(format!(
                                            "{:.1}%  ·  {}  ·  {}",
                                            snap.percentage,
                                            eta_text,
                                            format_duration(snap.elapsed_seconds as u64)
                                        ))
                                        .small()
                                        .strong(),
                                    );
                                });
                            });
                            ui.add_space(3.0);
                            ui.add(
                                egui::ProgressBar::new(snap.percentage / 100.0)
                                    .desired_height(10.0)
                                    .animate(true),
                            );
                            ui.add_space(2.0);
                            ui.horizontal(|ui| {
                                ui.label(
                                    RichText::new(format!(
                                        "PC {}개 · 외장 {}개 ({})",
                                        snap.discovered_pc_files,
                                        snap.discovered_ext_files,
                                        format_size(snap.discovered_bytes)
                                    ))
                                    .small()
                                    .color(TEXT_MUTED),
                                );
                                if !snap.current_file.is_empty() {
                                    ui.label(
                                        RichText::new(format!("·  {}", snap.current_file))
                                            .small()
                                            .color(TEXT_MUTED),
                                    );
                                }
                            });
                        });
                }

                // 토스트 알림
                if let Some(toast) = self.status_toast.clone() {
                    ui.add_space(4.0);
                    Frame::none()
                        .fill(BG_SURFACE)
                        .stroke(Stroke::new(1.0_f32, BORDER_LIGHT))
                        .rounding(Rounding::same(4.0))
                        .inner_margin(Margin::symmetric(8.0, 4.0))
                        .show(ui, |ui| {
                            ui.horizontal(|ui| {
                                ui.label(RichText::new(toast).small().color(ACCENT_BLUE));
                                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                                    if ui.small_button("×").clicked() {
                                        self.status_toast = None;
                                    }
                                });
                            });
                        });
                }
            });
    }

    fn render_left_navigation(&mut self, ui: &mut egui::Ui) {
        ui.add_space(8.0);

        let summary = self
            .result
            .as_ref()
            .map(|r| r.summary.clone())
            .unwrap_or_default();

        let nav_items: [(NavTab, Option<(String, String)>); 7] = [
            (NavTab::Dashboard, None),
            (
                NavTab::UnbackedPc,
                Some((
                    format!("{}", summary.unbacked_pc_files),
                    format!("총 {}", format_size(summary.unbacked_pc_bytes)),
                )),
            ),
            (
                NavTab::BackedUpPc,
                Some((
                    format!("{}", summary.backed_up_pc_files),
                    format!("총 {}", format_size(summary.backed_up_pc_bytes)),
                )),
            ),
            (
                NavTab::ExtDuplicates,
                Some((
                    format!("{}", summary.ext_duplicate_groups),
                    format!("낭비 {}", format_size(summary.ext_wasted_bytes)),
                )),
            ),
            (
                NavTab::CrossDuplicates,
                Some((
                    format!("{}", summary.cross_duplicate_groups),
                    format!("{}개 동일 그룹", summary.cross_duplicate_groups),
                )),
            ),
            (
                NavTab::PcDuplicates,
                Some((
                    format!("{}", summary.pc_duplicate_groups),
                    format!("낭비 {}", format_size(summary.pc_wasted_bytes)),
                )),
            ),
            (
                NavTab::ExtOnly,
                Some((
                    format!("{}", summary.ext_only_files),
                    format!("총 {}", format_size(summary.ext_only_bytes)),
                )),
            ),
        ];

        for (tab, badge_opt) in nav_items {
            let is_selected = self.nav_tab == tab;
            let row_height = 30.0;
            let (rect, resp) = ui.allocate_exact_size(
                Vec2::new(ui.available_width(), row_height),
                egui::Sense::click(),
            );

            let resp = resp.on_hover_cursor(CursorIcon::PointingHand);
            let hovered = resp.hovered();

            let bg = if is_selected {
                ACCENT_BLUE_BG
            } else if hovered {
                Color32::from_rgb(236, 240, 244)
            } else {
                Color32::TRANSPARENT
            };

            ui.painter().rect_filled(rect, Rounding::same(4.0), bg);

            if is_selected {
                let accent_rect = egui::Rect::from_min_size(
                    egui::pos2(rect.left() + 2.0, rect.top() + 6.0),
                    Vec2::new(3.0, row_height - 12.0),
                );
                ui.painter()
                    .rect_filled(accent_rect, Rounding::same(2.0), ACCENT_BLUE);
            }

            let text_color = if is_selected {
                ACCENT_BLUE
            } else {
                TEXT_PRIMARY
            };

            ui.painter().text(
                egui::pos2(rect.left() + 12.0, rect.center().y),
                egui::Align2::LEFT_CENTER,
                tab.short_title(),
                egui::FontId::proportional(13.5),
                text_color,
            );

            let mut full_tooltip = tab.tooltip().to_string();
            if let (Some((cnt_str, size_hint)), true) = (badge_opt, self.result.is_some()) {
                full_tooltip = format!("{}\n\n항목: {} ({})", full_tooltip, cnt_str, size_hint);
                ui.painter().text(
                    egui::pos2(rect.right() - 10.0, rect.center().y),
                    egui::Align2::RIGHT_CENTER,
                    cnt_str,
                    egui::FontId::proportional(11.5),
                    if is_selected { ACCENT_BLUE } else { TEXT_MUTED },
                );
            }

            if resp.on_hover_text(full_tooltip).clicked() {
                self.nav_tab = tab;
                self.subfolder_filter = None;
            }
        }

        if let Some(ref res) = self.result {
            ui.add_space(8.0);
            ui.separator();
            ui.add_space(4.0);

            ui.horizontal(|ui| {
                ui.label(
                    RichText::new("폴더 필터")
                        .small()
                        .strong()
                        .color(TEXT_MUTED),
                )
                .on_hover_text("특정 하위 폴더에 속한 파일만 골라서 봅니다.");

                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    if self.subfolder_filter.is_some()
                        && ui
                            .small_button("해제")
                            .on_hover_text("폴더 필터를 해제하고 모든 폴더를 봅니다.")
                            .clicked()
                    {
                        self.subfolder_filter = None;
                    }
                });
            });

            let all_sel = self.subfolder_filter.is_none();
            if ui
                .selectable_label(all_sel, "전체 폴더")
                .on_hover_cursor(CursorIcon::PointingHand)
                .clicked()
            {
                self.subfolder_filter = None;
            }

            let folders = match self.nav_tab {
                NavTab::ExtOnly | NavTab::ExtDuplicates => &res.ext_folders,
                _ => &res.pc_folders,
            };

            let mut clicked_folder: Option<String> = None;
            ScrollArea::vertical()
                .id_salt("left_folder_tree_virtual")
                .show_rows(ui, 21.0, folders.len(), |ui, row_range| {
                    for idx in row_range {
                        if let Some(folder) = folders.get(idx) {
                            let sel = self.subfolder_filter.as_ref() == Some(folder);
                            if ui
                                .selectable_label(sel, format!("  {}", folder))
                                .on_hover_text(folder)
                                .on_hover_cursor(CursorIcon::PointingHand)
                                .clicked()
                            {
                                clicked_folder = Some(folder.clone());
                            }
                        }
                    }
                });

            if let Some(f) = clicked_folder {
                self.subfolder_filter = Some(f);
            }
        }
    }

    fn render_dashboard(&mut self, ui: &mut egui::Ui) {
        let Some(ref res) = self.result else {
            ui.centered_and_justified(|ui| {
                ui.vertical_centered(|ui| {
                    ui.add_space(120.0);
                    ui.label(
                        RichText::new("백업 & 중복 파일 탐색기")
                            .size(22.0)
                            .strong()
                            .color(TEXT_PRIMARY),
                    );
                    ui.add_space(8.0);
                    ui.label(
                        RichText::new("상단에서 내 PC 폴더와 외장하드 폴더를 지정하고 [분석 시작]을 누르세요.")
                            .color(TEXT_MUTED),
                    );
                    ui.add_space(4.0);
                    ui.label(
                        RichText::new("폴더 구조나 파일명이 달라도 실제 파일 내용으로 정확히 비교합니다.")
                            .small()
                            .color(TEXT_MUTED),
                    );
                });
            });
            return;
        };

        let s = &res.summary;

        ScrollArea::vertical().show(ui, |ui| {
            ui.add_space(4.0);
            ui.horizontal(|ui| {
                ui.label(RichText::new("분석 요약").size(18.0).strong());
                ui.label(
                    RichText::new(format!("({:.1}초 소요)", s.elapsed_secs))
                        .small()
                        .color(TEXT_MUTED),
                );
            });
            ui.add_space(10.0);

            ui.columns(4, |cols| {
                let cards = [
                    (
                        "미백업 (PC)",
                        format!("{}개", s.unbacked_pc_files),
                        format_size(s.unbacked_pc_bytes),
                        COLOR_WARN,
                        NavTab::UnbackedPc,
                        "외장하드에 백업되어 있지 않은 내 PC 파일입니다. 클릭하여 목록을 확인하세요.",
                    ),
                    (
                        "백업 완료",
                        format!("{}개", s.backed_up_pc_files),
                        format_size(s.backed_up_pc_bytes),
                        COLOR_SUCCESS,
                        NavTab::BackedUpPc,
                        "내 PC와 외장하드 양쪽에 모두 존재하는 안전한 파일입니다.",
                    ),
                    (
                        "외장 중복 낭비",
                        format!("{}그룹", s.ext_duplicate_groups),
                        format_size(s.ext_wasted_bytes),
                        COLOR_DANGER,
                        NavTab::ExtDuplicates,
                        "외장하드 안에 여러 번 중복 저장되어 추가로 차지하는 용량입니다. 클릭하여 정리할 수 있습니다.",
                    ),
                    (
                        "외장 전용",
                        format!("{}개", s.ext_only_files),
                        format_size(s.ext_only_bytes),
                        ACCENT_BLUE,
                        NavTab::ExtOnly,
                        "내 PC에는 없고 외장하드에만 보관 중인 파일입니다.",
                    ),
                ];

                for (i, (title, main_val, sub_val, accent, target_tab, tip)) in
                    cards.into_iter().enumerate()
                {
                    let frame_resp = Frame::none()
                        .fill(BG_SUBTLE)
                        .stroke(Stroke::new(1.0_f32, BORDER_LIGHT))
                        .rounding(Rounding::same(6.0))
                        .inner_margin(Margin::same(14.0))
                        .show(&mut cols[i], |ui| {
                            ui.set_width(ui.available_width());
                            ui.label(RichText::new(title).small().strong().color(accent));
                            ui.add_space(6.0);
                            ui.label(RichText::new(sub_val).size(20.0).strong().color(TEXT_PRIMARY));
                            ui.label(RichText::new(main_val).small().color(TEXT_MUTED));
                        })
                        .response
                        .interact(egui::Sense::click())
                        .on_hover_text(tip)
                        .on_hover_cursor(CursorIcon::PointingHand);

                    if frame_resp.clicked() {
                        self.nav_tab = target_tab;
                    }
                }
            });

            ui.add_space(18.0);
            ui.label(RichText::new("유형별 현황").size(14.0).strong());
            ui.add_space(6.0);

            TableBuilder::new(ui)
                .striped(true)
                .cell_layout(Layout::left_to_right(Align::Center))
                .column(Column::initial(110.0))
                .column(Column::initial(160.0))
                .column(Column::initial(160.0))
                .column(Column::initial(160.0))
                .column(Column::remainder())
                .header(26.0, |mut header| {
                    header.col(|ui| {
                        ui.label(RichText::new("유형").small().strong().color(TEXT_MUTED));
                    });
                    header.col(|ui| {
                        ui.label(RichText::new("미백업").small().strong().color(TEXT_MUTED));
                    });
                    header.col(|ui| {
                        ui.label(RichText::new("백업 완료").small().strong().color(TEXT_MUTED));
                    });
                    header.col(|ui| {
                        ui.label(RichText::new("외장하드").small().strong().color(TEXT_MUTED));
                    });
                    header.col(|ui| {
                        ui.label(RichText::new("바로가기").small().strong().color(TEXT_MUTED));
                    });
                })
                .body(|mut body| {
                    for cat in [
                        FileCategory::Photo,
                        FileCategory::Video,
                        FileCategory::Document,
                        FileCategory::Audio,
                        FileCategory::Archive,
                        FileCategory::Other,
                    ] {
                        let mut unbacked_cnt = 0usize;
                        let mut unbacked_sz = 0u64;
                        let mut backed_cnt = 0usize;
                        let mut backed_sz = 0u64;
                        let mut ext_cnt = 0usize;
                        let mut ext_sz = 0u64;

                        for f in &res.files {
                            if f.category != cat {
                                continue;
                            }
                            match f.location {
                                LocationKind::Pc => match f.status {
                                    BackupStatus::UnbackedOnPc => {
                                        unbacked_cnt += 1;
                                        unbacked_sz += f.size;
                                    }
                                    BackupStatus::BackedUp => {
                                        backed_cnt += 1;
                                        backed_sz += f.size;
                                    }
                                    _ => {}
                                },
                                LocationKind::External => {
                                    ext_cnt += 1;
                                    ext_sz += f.size;
                                }
                            }
                        }

                        body.row(28.0, |mut row| {
                            row.col(|ui| {
                                ui.label(category_short_label(cat))
                                    .on_hover_text(category_tooltip(cat));
                            });
                            row.col(|ui| {
                                ui.label(format!("{}개 · {}", unbacked_cnt, format_size(unbacked_sz)));
                            });
                            row.col(|ui| {
                                ui.label(format!("{}개 · {}", backed_cnt, format_size(backed_sz)));
                            });
                            row.col(|ui| {
                                ui.label(format!("{}개 · {}", ext_cnt, format_size(ext_sz)));
                            });
                            row.col(|ui| {
                                if ui.small_button("미백업").clicked() {
                                    self.category_filter = cat;
                                    self.nav_tab = NavTab::UnbackedPc;
                                }
                                if ui.small_button("외장 중복").clicked() {
                                    self.category_filter = cat;
                                    self.nav_tab = NavTab::ExtDuplicates;
                                }
                            });
                        });
                    }
                });
        });
    }

    fn ensure_file_table_cache_updated(&mut self) {
        let current_key = TableCacheKey {
            result_version: self.result_version,
            nav_tab: self.nav_tab,
            category_filter: self.category_filter,
            custom_ext_filter: self.custom_ext_filter.clone(),
            search_query: self.search_query.clone(),
            subfolder_filter: self.subfolder_filter.clone(),
            sort_col: self.sort_col,
            sort_asc: self.sort_asc,
        };

        if self.table_cache_key.as_ref() == Some(&current_key) {
            return;
        }

        let Some(ref res) = self.result else {
            self.cached_filtered_ids.clear();
            self.cached_filtered_bytes = 0;
            self.table_cache_key = Some(current_key);
            return;
        };

        let target_ids = match self.nav_tab {
            NavTab::UnbackedPc => &res.unbacked_pc_ids,
            NavTab::BackedUpPc => &res.backed_up_pc_ids,
            NavTab::ExtOnly => &res.ext_only_ids,
            _ => &res.unbacked_pc_ids,
        };

        let mut filtered_ids: Vec<usize> = target_ids
            .iter()
            .copied()
            .filter(|&id| self.matches_filters(&res.files[id]))
            .collect();

        let sort_col = self.sort_col;
        let sort_asc = self.sort_asc;
        filtered_ids.sort_by(|&a, &b| {
            let fa = &res.files[a];
            let fb = &res.files[b];
            let ord = match sort_col {
                SortColumn::Name => fa.name.cmp(&fb.name),
                SortColumn::Size => fa.size.cmp(&fb.size),
                SortColumn::Extension => fa.extension.cmp(&fb.extension),
                SortColumn::Modified => fa.modified_ts.cmp(&fb.modified_ts),
                SortColumn::Path => fa.path.cmp(&fb.path),
                SortColumn::Copies => (fa.pc_copy_count + fa.ext_copy_count)
                    .cmp(&(fb.pc_copy_count + fb.ext_copy_count)),
            };
            if sort_asc {
                ord
            } else {
                ord.reverse()
            }
        });

        self.cached_filtered_bytes = filtered_ids.iter().map(|&id| res.files[id].size).sum();
        self.cached_filtered_ids = filtered_ids;
        self.table_cache_key = Some(current_key);
    }

    fn render_file_table(&mut self, ui: &mut egui::Ui) {
        self.ensure_file_table_cache_updated();

        let Some(ref res) = self.result else {
            return;
        };

        ui.horizontal(|ui| {
            ui.label(RichText::new(self.nav_tab.short_title()).strong())
                .on_hover_text(self.nav_tab.tooltip());
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                ui.label(
                    RichText::new(format!(
                        "{}개  ·  {}",
                        self.cached_filtered_ids.len(),
                        format_size(self.cached_filtered_bytes)
                    ))
                    .small()
                    .color(TEXT_MUTED),
                );
            });
        });
        ui.separator();

        let row_count = self.cached_filtered_ids.len();

        TableBuilder::new(ui)
            .striped(true)
            .resizable(true)
            .vscroll(true)
            .cell_layout(Layout::left_to_right(Align::Center))
            .column(Column::initial(230.0).at_least(120.0))
            .column(Column::initial(85.0).at_least(65.0))
            .column(Column::initial(70.0).at_least(55.0))
            .column(Column::initial(115.0).at_least(95.0))
            .column(Column::initial(105.0).at_least(85.0))
            .column(Column::remainder().at_least(200.0))
            .header(26.0, |mut header| {
                let mut sort_btn = |ui: &mut egui::Ui, label: &str, col: SortColumn| {
                    let arrow = if self.sort_col == col {
                        if self.sort_asc {
                            " ▲"
                        } else {
                            " ▼"
                        }
                    } else {
                        ""
                    };
                    if ui
                        .add(
                            egui::Button::new(
                                RichText::new(format!("{}{}", label, arrow))
                                    .small()
                                    .strong()
                                    .color(TEXT_MUTED),
                            )
                            .fill(Color32::TRANSPARENT)
                            .stroke(Stroke::NONE),
                        )
                        .clicked()
                    {
                        if self.sort_col == col {
                            self.sort_asc = !self.sort_asc;
                        } else {
                            self.sort_col = col;
                            self.sort_asc = col == SortColumn::Name;
                        }
                    }
                };

                header.col(|ui| sort_btn(ui, "이름", SortColumn::Name));
                header.col(|ui| sort_btn(ui, "크기", SortColumn::Size));
                header.col(|ui| sort_btn(ui, "유형", SortColumn::Extension));
                header.col(|ui| sort_btn(ui, "수정한 날짜", SortColumn::Modified));
                header.col(|ui| sort_btn(ui, "보유 (PC/외장)", SortColumn::Copies));
                header.col(|ui| sort_btn(ui, "경로", SortColumn::Path));
            })
            .body(|body| {
                body.rows(27.0, row_count, |mut row| {
                    let file_id = self.cached_filtered_ids[row.index()];
                    let entry = &res.files[file_id];
                    let is_selected = self.selected_file_id == Some(file_id);
                    row.set_selected(is_selected);

                    row.col(|ui| {
                        let resp = ui.selectable_label(is_selected, &entry.name);
                        if resp.clicked() {
                            self.selected_file_id = Some(file_id);
                        }
                        if resp.double_clicked() {
                            open_in_windows_explorer(&entry.path);
                        }
                    });

                    row.col(|ui| {
                        if ui
                            .selectable_label(is_selected, format_size(entry.size))
                            .clicked()
                        {
                            self.selected_file_id = Some(file_id);
                        }
                    });

                    row.col(|ui| {
                        let ext_txt = if entry.extension.is_empty() {
                            "-".to_string()
                        } else {
                            entry.extension.to_uppercase()
                        };
                        if ui
                            .selectable_label(is_selected, RichText::new(ext_txt).small().color(TEXT_MUTED))
                            .clicked()
                        {
                            self.selected_file_id = Some(file_id);
                        }
                    });

                    row.col(|ui| {
                        if ui
                            .selectable_label(
                                is_selected,
                                RichText::new(&entry.modified_str).small().color(TEXT_MUTED),
                            )
                            .clicked()
                        {
                            self.selected_file_id = Some(file_id);
                        }
                    });

                    row.col(|ui| {
                        let badge = format!("PC {} · 외장 {}", entry.pc_copy_count, entry.ext_copy_count);
                        let color = if entry.ext_copy_count == 0 {
                            COLOR_WARN
                        } else if entry.ext_copy_count >= 2 {
                            COLOR_DANGER
                        } else {
                            COLOR_SUCCESS
                        };
                        if ui
                            .selectable_label(is_selected, RichText::new(badge).small().color(color))
                            .clicked()
                        {
                            self.selected_file_id = Some(file_id);
                        }
                    });

                    row.col(|ui| {
                        let resp = ui.selectable_label(
                            is_selected,
                            RichText::new(entry.path.to_string_lossy())
                                .small()
                                .color(TEXT_MUTED),
                        );
                        if resp.clicked() {
                            self.selected_file_id = Some(file_id);
                        }
                        if resp.double_clicked() {
                            open_in_windows_explorer(&entry.path);
                        }
                    });
                });
            });
    }

    fn render_duplicate_groups_table(&mut self, ui: &mut egui::Ui, mode: NavTab) {
        let Some(ref res) = self.result else {
            return;
        };

        let groups = match mode {
            NavTab::ExtDuplicates => &res.ext_duplicate_groups,
            NavTab::PcDuplicates => &res.pc_duplicate_groups,
            _ => &res.cross_duplicate_groups,
        };

        let filtered_groups: Vec<&scanner::DuplicateGroup> = groups
            .iter()
            .filter(|g| {
                let matches_basic = g
                    .pc_files
                    .iter()
                    .chain(g.ext_files.iter())
                    .any(|&fid| self.matches_filters(&res.files[fid]));
                if !matches_basic {
                    return false;
                }
                if self.only_show_marked_groups {
                    return g
                        .pc_files
                        .iter()
                        .chain(g.ext_files.iter())
                        .any(|fid| self.marked_for_cleanup.contains(fid));
                }
                true
            })
            .collect();

        let total_wasted: u64 = filtered_groups.iter().map(|g| g.wasted_ext_bytes).sum();
        let target_scope = if mode == NavTab::PcDuplicates {
            TargetScope::PcOnly
        } else {
            TargetScope::ExternalOnly
        };

        let mut trigger_rule: Option<SmartSelectRule> = None;
        let mut trigger_subfolder_select = false;
        let mut trigger_clear = false;

        ui.horizontal(|ui| {
            ui.label(RichText::new(self.nav_tab.short_title()).strong())
                .on_hover_text(self.nav_tab.tooltip());

            ui.separator();

            ui.label(RichText::new("스마트 선택:").small().color(TEXT_MUTED))
                .on_hover_text("규칙에 따라 정리할 중복 사본만 자동 체크합니다. 각 그룹의 원본 1개는 항상 안전하게 보호됩니다.");

            if ui
                .small_button("복사본명")
                .on_hover_text("파일명에 '(1)', '(2)', '복사본', 'copy' 등이 붙은 사본만 선택합니다.")
                .clicked()
            {
                trigger_rule = Some(SmartSelectRule::FilenameCopyPattern);
            }

            if ui
                .small_button("최신 사본")
                .on_hover_text("가장 오래된 원본 1개만 남기고, 나중에 복사된 최신 사본들을 선택합니다.")
                .clicked()
            {
                trigger_rule = Some(SmartSelectRule::KeepOldestDeleteNewer);
            }

            if ui
                .small_button("오래된 사본")
                .on_hover_text("가장 최근 날짜의 파일 1개만 남기고, 오래된 사본들을 선택합니다.")
                .clicked()
            {
                trigger_rule = Some(SmartSelectRule::KeepNewestDeleteOlder);
            }

            if ui
                .small_button("깊은 폴더")
                .on_hover_text("상위(얕은) 폴더의 파일 1개만 남기고, 깊은 하위 폴더의 중복 사본들을 선택합니다.")
                .clicked()
            {
                trigger_rule = Some(SmartSelectRule::KeepShallowestDeleteDeeper);
            }

            if ui
                .small_button("상위 폴더")
                .on_hover_text("세부(깊은) 하위 폴더에 정리된 파일 1개만 남기고, 상위 폴더에 흩어진 사본들을 선택합니다.")
                .clicked()
            {
                trigger_rule = Some(SmartSelectRule::KeepDeepestDeleteShallower);
            }

            if let Some(ref sub) = self.subfolder_filter {
                if ui
                    .small_button("이 폴더만")
                    .on_hover_text(format!("현재 필터된 '{}' 폴더 안의 중복 사본만 일괄 선택합니다.", sub))
                    .clicked()
                {
                    trigger_subfolder_select = true;
                }
            }

            if !self.marked_for_cleanup.is_empty() {
                ui.separator();
                ui.checkbox(&mut self.only_show_marked_groups, "선택됨만")
                    .on_hover_text("정리 대상으로 체크된 중복 그룹만 모아서 봅니다.");
                if ui
                    .small_button("해제")
                    .on_hover_text("모든 정리 선택을 해제합니다.")
                    .clicked()
                {
                    trigger_clear = true;
                }
            }

            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                if mode == NavTab::ExtDuplicates || mode == NavTab::PcDuplicates {
                    ui.label(
                        RichText::new(format!(
                            "{}그룹 · 낭비 {}",
                            filtered_groups.len(),
                            format_size(total_wasted)
                        ))
                        .small()
                        .strong()
                        .color(COLOR_DANGER),
                    );
                } else {
                    ui.label(
                        RichText::new(format!("{}그룹", filtered_groups.len()))
                            .small()
                            .color(TEXT_MUTED),
                    );
                }
            });
        });

        if let Some(rule) = trigger_rule {
            let count = apply_smart_selection(
                &filtered_groups,
                &res.files,
                target_scope,
                rule,
                &mut self.marked_for_cleanup,
            );
            let sz = self.marked_total_bytes();
            self.status_toast = Some(format!(
                "{}개 사본 선택됨 ({}) — 각 그룹 원본 1개 이상 자동 보호",
                count,
                format_size(sz)
            ));
        }
        if trigger_subfolder_select {
            if let Some(sub) = self.subfolder_filter.clone() {
                let count = select_duplicates_in_subfolder(
                    &filtered_groups,
                    &res.files,
                    target_scope,
                    &sub,
                    &mut self.marked_for_cleanup,
                );
                self.status_toast = Some(format!("'{}' 내 {}개 선택됨", sub, count));
            }
        }
        if trigger_clear {
            self.marked_for_cleanup.clear();
            self.only_show_marked_groups = false;
        }

        ui.separator();

        TableBuilder::new(ui)
            .striped(true)
            .resizable(true)
            .vscroll(true)
            .cell_layout(Layout::left_to_right(Align::Center))
            .column(Column::initial(220.0).at_least(130.0))
            .column(Column::initial(85.0).at_least(65.0))
            .column(Column::initial(95.0).at_least(75.0))
            .column(Column::initial(95.0).at_least(80.0))
            .column(Column::initial(115.0).at_least(95.0))
            .column(Column::remainder().at_least(220.0))
            .header(26.0, |mut header| {
                header.col(|ui| {
                    ui.label(RichText::new("대표 파일명").small().strong().color(TEXT_MUTED));
                });
                header.col(|ui| {
                    ui.label(RichText::new("크기").small().strong().color(TEXT_MUTED));
                });
                header.col(|ui| {
                    ui.label(RichText::new("낭비 용량").small().strong().color(TEXT_MUTED));
                });
                header.col(|ui| {
                    ui.label(RichText::new("보유").small().strong().color(TEXT_MUTED));
                });
                header.col(|ui| {
                    ui.label(RichText::new("정리 상태").small().strong().color(TEXT_MUTED));
                });
                header.col(|ui| {
                    ui.label(RichText::new("저장 위치").small().strong().color(TEXT_MUTED));
                });
            })
            .body(|body| {
                body.rows(27.0, filtered_groups.len(), |mut row| {
                    let g = filtered_groups[row.index()];
                    let is_selected = self.selected_dup_hash.as_deref() == Some(&g.hash);
                    row.set_selected(is_selected);

                    let first_file_id = g
                        .ext_files
                        .first()
                        .or_else(|| g.pc_files.first())
                        .copied();

                    let marked_in_group = g
                        .pc_files
                        .iter()
                        .chain(g.ext_files.iter())
                        .filter(|id| self.marked_for_cleanup.contains(id))
                        .count();
                    let total_in_group = g.pc_files.len() + g.ext_files.len();

                    let mut select_row = || {
                        self.selected_dup_hash = Some(g.hash.clone());
                        if let Some(fid) = first_file_id {
                            self.selected_file_id = Some(fid);
                        }
                    };

                    row.col(|ui| {
                        let resp = ui.selectable_label(is_selected, &g.representative_name);
                        if resp.clicked() {
                            select_row();
                        }
                        if resp.double_clicked() {
                            select_row();
                            if let Some(fid) = first_file_id {
                                open_in_windows_explorer(&res.files[fid].path);
                            }
                        }
                    });

                    row.col(|ui| {
                        if ui
                            .selectable_label(is_selected, format_size(g.file_size))
                            .clicked()
                        {
                            select_row();
                        }
                    });

                    row.col(|ui| {
                        let txt = if g.wasted_ext_bytes > 0 {
                            format_size(g.wasted_ext_bytes)
                        } else {
                            "-".to_string()
                        };
                        if ui
                            .selectable_label(
                                is_selected,
                                RichText::new(txt).small().strong().color(COLOR_DANGER),
                            )
                            .clicked()
                        {
                            select_row();
                        }
                    });

                    row.col(|ui| {
                        if ui
                            .selectable_label(
                                is_selected,
                                RichText::new(format!("PC {} · 외장 {}", g.pc_files.len(), g.ext_files.len()))
                                    .small()
                                    .color(TEXT_MUTED),
                            )
                            .clicked()
                        {
                            select_row();
                        }
                    });

                    row.col(|ui| {
                        let status_txt = if marked_in_group > 0 {
                            RichText::new(format!(
                                "정리 {} · 보존 {}",
                                marked_in_group,
                                total_in_group.saturating_sub(marked_in_group)
                            ))
                            .small()
                            .strong()
                            .color(COLOR_WARN)
                        } else {
                            RichText::new("-").small().color(TEXT_MUTED)
                        };
                        if ui.selectable_label(is_selected, status_txt).clicked() {
                            select_row();
                        }
                    });

                    row.col(|ui| {
                        let all_ids: Vec<usize> = g
                            .pc_files
                            .iter()
                            .chain(g.ext_files.iter())
                            .copied()
                            .collect();

                        if marked_in_group > 0 {
                            let kept_paths: Vec<String> = all_ids
                                .iter()
                                .filter(|id| !self.marked_for_cleanup.contains(id))
                                .take(2)
                                .map(|&fid| res.files[fid].path.to_string_lossy().to_string())
                                .collect();
                            let marked_paths: Vec<String> = all_ids
                                .iter()
                                .filter(|id| self.marked_for_cleanup.contains(id))
                                .take(2)
                                .map(|&fid| res.files[fid].path.to_string_lossy().to_string())
                                .collect();

                            ui.horizontal(|ui| {
                                if ui
                                    .selectable_label(
                                        is_selected,
                                        RichText::new(format!("[정리] {}", marked_paths.join(", ")))
                                            .small()
                                            .color(COLOR_WARN),
                                    )
                                    .clicked()
                                {
                                    select_row();
                                }
                                ui.label(RichText::new("·").small().color(BORDER_LIGHT));
                                if ui
                                    .selectable_label(
                                        is_selected,
                                        RichText::new(format!("[보존] {}", kept_paths.join(", ")))
                                            .small()
                                            .color(COLOR_SUCCESS),
                                    )
                                    .clicked()
                                {
                                    select_row();
                                }
                            });
                        } else {
                            let preview_paths: Vec<String> = all_ids
                                .iter()
                                .take(3)
                                .map(|&fid| res.files[fid].path.to_string_lossy().to_string())
                                .collect();
                            if ui
                                .selectable_label(
                                    is_selected,
                                    RichText::new(preview_paths.join("  ·  "))
                                        .small()
                                        .color(TEXT_MUTED),
                                )
                                .clicked()
                            {
                                select_row();
                            }
                        }
                    });
                });
            });
    }

    fn render_right_details_pane(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let Some(selected_id) = self.selected_file_id else {
            ui.centered_and_justified(|ui| {
                ui.label(
                    RichText::new("파일을 선택하면 상세 정보와\n미리보기가 표시됩니다.")
                        .small()
                        .color(TEXT_MUTED),
                );
            });
            return;
        };

        let Some(entry) = self
            .result
            .as_ref()
            .and_then(|r| r.files.get(selected_id))
            .cloned()
        else {
            return;
        };

        if is_previewable_image(&entry.extension) {
            self.request_async_image_preview(entry.path.clone());
            self.poll_async_image_preview(ctx);
        } else {
            self.image_preview = None;
        }

        let Some(ref res) = self.result else {
            return;
        };

        let mut toggle_id: Option<usize> = None;

        ScrollArea::vertical().show(ui, |ui| {
            ui.add_space(4.0);
            ui.label(RichText::new(&entry.name).strong().size(14.5));
            ui.label(
                RichText::new(format!(
                    "{}  ·  {}  ·  {}",
                    format_size(entry.size),
                    entry.modified_str,
                    entry.status.label()
                ))
                .small()
                .color(TEXT_MUTED),
            );
            ui.add_space(6.0);

            if let Some(ref preview) = self.image_preview {
                Frame::none()
                    .fill(BG_SURFACE)
                    .stroke(Stroke::new(1.0_f32, BORDER_LIGHT))
                    .rounding(Rounding::same(6.0))
                    .inner_margin(Margin::same(8.0))
                    .show(ui, |ui| {
                        ui.vertical_centered(|ui| {
                            if preview.is_loading {
                                ctx.request_repaint_after(std::time::Duration::from_millis(50));
                                ui.add_space(16.0);
                                ui.spinner();
                                ui.add_space(16.0);
                            } else if let Some(ref tex) = preview.texture {
                                let max_w = (ui.available_width() - 12.0).max(120.0);
                                let tex_size = tex.size_vec2();
                                let scale = (max_w / tex_size.x).min(190.0 / tex_size.y).min(1.0);
                                ui.image((tex.id(), tex_size * scale));
                                if let Some((w, h)) = preview.dimensions {
                                    ui.label(
                                        RichText::new(format!("{} × {} px", w, h))
                                            .small()
                                            .color(TEXT_MUTED),
                                    );
                                }
                            } else if let Some(ref err) = preview.error {
                                ui.label(RichText::new(err).small().color(TEXT_MUTED));
                            }
                        });
                    });
                ui.add_space(8.0);
            }

            ui.separator();
            ui.add_space(6.0);

            let mut all_same_ids = vec![entry.id];
            all_same_ids.extend(entry.matching_ids.iter().copied());
            all_same_ids.sort_unstable();
            all_same_ids.dedup();

            let total_copies = all_same_ids.len();

            let pc_same: Vec<&scanner::FileEntry> = all_same_ids
                .iter()
                .filter_map(|&id| res.files.get(id))
                .filter(|f| f.location == LocationKind::Pc)
                .collect();

            let ext_same: Vec<&scanner::FileEntry> = all_same_ids
                .iter()
                .filter_map(|&id| res.files.get(id))
                .filter(|f| f.location == LocationKind::External)
                .collect();

            let render_location_cards =
                |ui: &mut egui::Ui,
                 header_title: &str,
                 header_color: Color32,
                 items: &[&scanner::FileEntry],
                 empty_text: &str,
                 marked_set: &HashSet<usize>,
                 toggle_out: &mut Option<usize>| {
                    ui.label(
                        RichText::new(format!("{} ({})", header_title, items.len()))
                            .small()
                            .strong()
                            .color(header_color),
                    );
                    ui.add_space(4.0);
                    if items.is_empty() {
                        ui.label(RichText::new(empty_text).small().color(TEXT_MUTED));
                        return;
                    }

                    for f in items {
                        let is_marked = marked_set.contains(&f.id);
                        let card_bg = if is_marked {
                            Color32::from_rgb(255, 245, 242)
                        } else {
                            BG_SURFACE
                        };
                        let card_stroke = if is_marked {
                            Stroke::new(1.2_f32, COLOR_WARN)
                        } else {
                            Stroke::new(1.0_f32, BORDER_LIGHT)
                        };

                        Frame::none()
                            .fill(card_bg)
                            .stroke(card_stroke)
                            .rounding(Rounding::same(5.0))
                            .inner_margin(Margin::same(8.0))
                            .show(ui, |ui| {
                                ui.horizontal(|ui| {
                                    if total_copies >= 2 {
                                        let mut checked = is_marked;
                                        if ui
                                            .checkbox(
                                                &mut checked,
                                                RichText::new(if is_marked {
                                                    "정리 대상"
                                                } else {
                                                    "원본 보존"
                                                })
                                                .small()
                                                .strong()
                                                .color(if is_marked {
                                                    COLOR_WARN
                                                } else {
                                                    COLOR_SUCCESS
                                                }),
                                            )
                                            .on_hover_text("체크하면 이 사본을 정리(격리/휴지통 이동) 대상에 포함합니다. (최소 1개 원본은 자동 보호)")
                                            .changed()
                                        {
                                            *toggle_out = Some(f.id);
                                        }
                                    }
                                    if is_copy_filename_pattern(&f.name) {
                                        ui.label(
                                            RichText::new("복사본명")
                                                .small()
                                                .color(COLOR_WARN),
                                        );
                                    }

                                    ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                                        if ui
                                            .small_button("복사")
                                            .on_hover_text("전체 경로 복사")
                                            .clicked()
                                        {
                                            ui.output_mut(|o| {
                                                o.copied_text =
                                                    f.path.to_string_lossy().to_string();
                                            });
                                        }
                                        if ui
                                            .small_button("열기")
                                            .on_hover_text("기본 앱으로 파일 바로 열기")
                                            .clicked()
                                        {
                                            open_file_directly(&f.path);
                                        }
                                        if ui
                                            .small_button("위치")
                                            .on_hover_text("윈도우 탐색기에서 위치 열기")
                                            .clicked()
                                        {
                                            open_in_windows_explorer(&f.path);
                                        }
                                    });
                                });

                                ui.label(RichText::new(&f.name).small().strong());
                                ui.label(
                                    RichText::new(f.path.to_string_lossy())
                                        .small()
                                        .color(TEXT_MUTED),
                                );
                            });
                        ui.add_space(4.0);
                    }
                };

            render_location_cards(
                ui,
                "내 PC",
                ACCENT_BLUE,
                &pc_same,
                "내 PC에 없음",
                &self.marked_for_cleanup,
                &mut toggle_id,
            );

            ui.add_space(8.0);

            render_location_cards(
                ui,
                "외장하드",
                COLOR_SUCCESS,
                &ext_same,
                "외장하드에 없음 (백업 필요)",
                &self.marked_for_cleanup,
                &mut toggle_id,
            );
        });

        if let Some(id) = toggle_id {
            self.toggle_file_cleanup_mark(id);
        }
    }

    fn render_cleanup_confirm_modal(&mut self, ctx: &egui::Context) {
        let (is_cleanup_running, processed, total, cur_file, finished_rep) = {
            if let Ok(mut p) = self.cleanup_progress.lock() {
                (
                    p.is_running,
                    p.processed,
                    p.total,
                    p.current_file.clone(),
                    p.finished_report.take(),
                )
            } else {
                (false, 0, 0, String::new(), None)
            }
        };

        if let Some(report) = finished_rep {
            if let Some(ref mut res) = self.result {
                apply_cleanup_report_to_result(res, &report.success_ids);
                self.result_version += 1;
            }
            self.marked_for_cleanup.clear();
            self.only_show_marked_groups = false;
            self.last_quarantine_used = report.quarantine_dir_used.clone();
            self.show_cleanup_confirm_modal = false;

            if let Some(err) = report.first_error {
                self.status_toast = Some(format!(
                    "{}개 정리 ({}), {}개 실패: {}",
                    report.success_count,
                    format_size(report.freed_bytes),
                    report.failed_count,
                    err
                ));
            } else {
                let action_desc = match self.cleanup_method {
                    CleanupMethod::MoveToQuarantine => "격리 보관함으로 이동",
                    CleanupMethod::MoveToRecycleBin => "휴지통으로 이동",
                };
                self.status_toast = Some(format!(
                    "{}개 ({}) {} 완료",
                    report.success_count,
                    format_size(report.freed_bytes),
                    action_desc
                ));
            }
            return;
        }

        if !self.show_cleanup_confirm_modal {
            return;
        }

        if is_cleanup_running {
            ctx.request_repaint_after(std::time::Duration::from_millis(50));
        }

        let mut open = self.show_cleanup_confirm_modal;
        let mut do_execute = false;
        let mut close_modal = false;

        let marked_count = self.marked_for_cleanup.len();
        let marked_bytes = self.marked_total_bytes();

        egui::Window::new("중복 파일 정리 확인")
            .open(&mut open)
            .collapsible(false)
            .resizable(true)
            .default_size([700.0, 480.0])
            .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
            .show(ctx, |ui| {
                if is_cleanup_running {
                    ui.vertical_centered(|ui| {
                        ui.add_space(40.0);
                        ui.label(RichText::new("파일을 안전하게 정리하고 있습니다...").strong().size(15.0));
                        ui.add_space(10.0);
                        let frac = if total > 0 {
                            processed as f32 / total as f32
                        } else {
                            0.0
                        };
                        ui.add(egui::ProgressBar::new(frac).show_percentage().animate(true));
                        ui.add_space(6.0);
                        ui.label(
                            RichText::new(format!("{} / {} 개 · {}", processed, total, cur_file))
                                .small()
                                .color(TEXT_MUTED),
                        );
                        ui.add_space(40.0);
                    });
                    return;
                }

                ui.horizontal(|ui| {
                    ui.label(
                        RichText::new(format!(
                            "{}개 파일 · {} 확보",
                            marked_count,
                            format_size(marked_bytes)
                        ))
                        .strong()
                        .size(15.0)
                        .color(COLOR_WARN),
                    );
                    ui.label(
                        RichText::new("(각 그룹 원본 1개 이상 안전 보존됨)")
                            .small()
                            .color(COLOR_SUCCESS),
                    );
                });

                ui.add_space(6.0);
                ui.separator();
                ui.add_space(6.0);

                ui.radio_value(
                    &mut self.cleanup_method,
                    CleanupMethod::MoveToQuarantine,
                    "임시 격리 폴더로 이동",
                )
                .on_hover_text("원래 드라이브와 폴더 구조를 그대로 유지한 채 격리 폴더로 이동합니다. 가장 빠르고 안전합니다.");

                if self.cleanup_method == CleanupMethod::MoveToQuarantine {
                    ui.horizontal(|ui| {
                        ui.add_space(20.0);
                        ui.label(
                            RichText::new(self.quarantine_root.to_string_lossy())
                                .small()
                                .color(TEXT_MUTED),
                        );
                        if ui.small_button("변경...").clicked() {
                            if let Some(dir) = rfd::FileDialog::new()
                                .set_title("임시 격리 폴더 선택")
                                .pick_folder()
                            {
                                self.quarantine_root = dir;
                            }
                        }
                    });
                }

                ui.radio_value(
                    &mut self.cleanup_method,
                    CleanupMethod::MoveToRecycleBin,
                    "윈도우 휴지통으로 이동",
                )
                .on_hover_text("윈도우 휴지통으로 안전하게 이동합니다. 휴지통에서 언제든 복원할 수 있습니다.");

                ui.add_space(6.0);
                ui.separator();
                ui.add_space(6.0);

                if let Some(ref res) = self.result {
                    let mut preview_ids: Vec<usize> =
                        self.marked_for_cleanup.iter().copied().collect();
                    preview_ids.sort_unstable();

                    ScrollArea::vertical()
                        .id_salt("cleanup_confirm_scroll")
                        .max_height(240.0)
                        .show(ui, |ui| {
                            for &fid in preview_ids.iter().take(100) {
                                let Some(f) = res.files.get(fid) else {
                                    continue;
                                };
                                let kept_path = f
                                    .matching_ids
                                    .iter()
                                    .find(|&&other_id| !self.marked_for_cleanup.contains(&other_id))
                                    .and_then(|&other_id| res.files.get(other_id))
                                    .map(|kf| kf.path.to_string_lossy().to_string())
                                    .unwrap_or_else(|| "원본 보존".to_string());

                                Frame::none()
                                    .fill(BG_SUBTLE)
                                    .rounding(Rounding::same(4.0))
                                    .inner_margin(Margin::symmetric(8.0, 5.0))
                                    .show(ui, |ui| {
                                        ui.label(
                                            RichText::new(format!(
                                                "[정리] {} ({})",
                                                f.path.to_string_lossy(),
                                                format_size(f.size)
                                            ))
                                            .small()
                                            .color(COLOR_DANGER),
                                        );
                                        ui.label(
                                            RichText::new(format!("[보존] {}", kept_path))
                                                .small()
                                                .color(COLOR_SUCCESS),
                                        );
                                    });
                                ui.add_space(3.0);
                            }
                        });
                }

                ui.add_space(10.0);
                ui.horizontal(|ui| {
                    let exec_btn = egui::Button::new(
                        RichText::new("정리 실행").strong().color(Color32::WHITE),
                    )
                    .fill(COLOR_SUCCESS)
                    .stroke(Stroke::NONE)
                    .min_size(Vec2::new(120.0, 32.0));

                    if ui.add(exec_btn).clicked() {
                        do_execute = true;
                    }

                    if ui
                        .add(egui::Button::new("취소").min_size(Vec2::new(80.0, 32.0)))
                        .clicked()
                    {
                        close_modal = true;
                    }
                });
            });

        if close_modal && !is_cleanup_running {
            open = false;
        }

        if do_execute {
            self.start_background_cleanup();
        }

        self.show_cleanup_confirm_modal = open;
    }
}

impl eframe::App for BackupExplorerApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        if self.is_scanning {
            ctx.request_repaint_after(std::time::Duration::from_millis(50));

            let stage = self
                .progress
                .lock()
                .map(|p| p.stage)
                .unwrap_or(ScanStage::Idle);

            if stage == ScanStage::Completed {
                if let Ok(mut slot) = self.scan_result_slot.lock() {
                    if let Some(res) = slot.take() {
                        self.result = Some(res);
                        self.result_version += 1;
                        self.table_cache_key = None;
                        self.is_scanning = false;
                        self.status_toast = Some("비교 분석이 완료되었습니다.".to_string());
                    }
                }
            } else if stage == ScanStage::Cancelled {
                self.is_scanning = false;
                self.status_toast = Some("분석이 중단되었습니다.".to_string());
            }
        }

        if !self.is_scanning && ctx.input(|i| i.key_pressed(Key::F5)) {
            self.start_scan();
        }

        self.render_cleanup_confirm_modal(ctx);

        egui::TopBottomPanel::top("win_explorer_ribbon")
            .frame(Frame::none().fill(BG_RIBBON))
            .show(ctx, |ui| {
                self.render_ribbon_top(ui);
            });

        egui::TopBottomPanel::bottom("win_explorer_status")
            .exact_height(26.0)
            .frame(
                Frame::none()
                    .fill(BG_RIBBON)
                    .stroke(Stroke::new(1.0_f32, BORDER_LIGHT))
                    .inner_margin(Margin::symmetric(12.0, 0.0)),
            )
            .show(ctx, |ui| {
                ui.horizontal_centered(|ui| {
                    if let Some(ref res) = self.result {
                        let s = &res.summary;
                        ui.label(
                            RichText::new(format!(
                                "PC {}개 ({})  ·  외장 {}개 ({})  ·  미백업 {}개 ({})  ·  외장 중복 낭비 {}",
                                s.total_pc_files,
                                format_size(s.total_pc_bytes),
                                s.total_ext_files,
                                format_size(s.total_ext_bytes),
                                s.unbacked_pc_files,
                                format_size(s.unbacked_pc_bytes),
                                format_size(s.ext_wasted_bytes)
                            ))
                            .small()
                            .color(TEXT_MUTED),
                        );
                    } else {
                        ui.label(RichText::new("준비됨").small().color(TEXT_MUTED));
                    }
                });
            });

        egui::SidePanel::left("win_explorer_nav")
            .default_width(200.0)
            .width_range(175.0..=290.0)
            .frame(
                Frame::none()
                    .fill(BG_SIDEBAR)
                    .stroke(Stroke::new(1.0_f32, BORDER_LIGHT))
                    .inner_margin(Margin::symmetric(8.0, 6.0)),
            )
            .show(ctx, |ui| {
                self.render_left_navigation(ui);
            });

        if self.nav_tab != NavTab::Dashboard && self.result.is_some() {
            egui::SidePanel::right("win_explorer_details")
                .default_width(320.0)
                .width_range(260.0..=460.0)
                .frame(
                    Frame::none()
                        .fill(BG_SIDEBAR)
                        .stroke(Stroke::new(1.0_f32, BORDER_LIGHT))
                        .inner_margin(Margin::same(10.0)),
                )
                .show(ctx, |ui| {
                    self.render_right_details_pane(ctx, ui);
                });
        }

        egui::CentralPanel::default()
            .frame(Frame::none().fill(BG_SURFACE).inner_margin(Margin::same(12.0)))
            .show(ctx, |ui| {
                if self.result.is_none() {
                    self.render_dashboard(ui);
                    return;
                }

                match self.nav_tab {
                    NavTab::Dashboard => self.render_dashboard(ui),
                    NavTab::UnbackedPc | NavTab::BackedUpPc | NavTab::ExtOnly => {
                        self.render_file_table(ui);
                    }
                    NavTab::ExtDuplicates | NavTab::CrossDuplicates | NavTab::PcDuplicates => {
                        let tab = self.nav_tab;
                        self.render_duplicate_groups_table(ui, tab);
                    }
                }
            });
    }
}

fn main() -> eframe::Result<()> {
    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("백업 & 중복 파일 탐색기")
            .with_inner_size([1340.0, 820.0])
            .with_min_inner_size([960.0, 600.0]),
        ..Default::default()
    };

    eframe::run_native(
        "Backup Explorer",
        native_options,
        Box::new(|cc| Ok(Box::new(BackupExplorerApp::new(cc)))),
    )
}
