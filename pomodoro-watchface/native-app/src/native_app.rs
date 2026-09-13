//! Native app registration: fixed 16-bit app id, launcher entry, one page.
//!
//! Mirrors the reference module (`native_app.rs` of Canopus-Module-BluetoothAudio):
//! stage 1 = `app_install(app_descriptor, pages, count)` + `app_lookup` sanity,
//! stage 2 = `launcher_add(APP_ID)` so the icon appears in the app list.
//! Descriptor layouts and the `APP_PACKAGE_OFFSET` come from the target pack.

use core::sync::atomic::{AtomicU32, Ordering};

#[cfg(feature = "device")]
use canopus_target_private::*;

pub const APP_ID: u16 = 0x0233; // "番茄钟" launcher app id (avoid 0x00CB used by headphones)
pub const PAGE_COUNT: usize = 1;
pub const PAGE_MAIN: usize = 0;

pub const PACKAGE_NAME: &[u8] = b"org.pomodoro.band9pro\0";
// UTF-8 "番茄钟"
pub const DISPLAY_NAME: &[u8] = &[0xE7, 0x95, 0xAA, 0xE8, 0x8C, 0x84, 0xE9, 0x92, 0x9F, 0];
pub const LAUNCHER_ICON: &[u8] = b"/data/canopus/appicon_pomodoro.bin\0";
const PAGE_NAME_MAIN: &[u8] = b"pomodoro\0";

#[cfg(feature = "device")]
static mut APP_DESCRIPTOR: core::mem::MaybeUninit<launcher_app_descriptor> =
    core::mem::MaybeUninit::uninit();
#[cfg(feature = "device")]
static mut PAGE_DESCRIPTORS: core::mem::MaybeUninit<[firmware_page_descriptor; PAGE_COUNT]> =
    core::mem::MaybeUninit::uninit();

#[cfg(feature = "device")]
pub fn page_descriptor_ptr(index: usize) -> *mut firmware_page_descriptor {
    // SAFETY: PAGE_DESCRIPTORS is initialized by `install_stage` before any
    // page lifecycle callback can run; the firmware only reads these
    // descriptors after install returns.
    unsafe {
        core::ptr::addr_of_mut!(PAGE_DESCRIPTORS)
            .cast::<firmware_page_descriptor>()
            .add(index)
    }
}

#[cfg(feature = "device")]
extern "C" fn launcher_display_name() -> *const u8 {
    DISPLAY_NAME.as_ptr()
}

#[cfg(feature = "device")]
fn c_str_equal(a: *const u8, expected: &[u8]) -> bool {
    if a.is_null() || expected.last() != Some(&0) {
        return false;
    }
    let mut i = 0usize;
    while i < expected.len() {
        if unsafe { *a.add(i) } != expected[i] {
            return false;
        }
        i += 1;
    }
    true
}

#[cfg(feature = "device")]
fn app_descriptor_init() {
    // SAFETY: APP_DESCRIPTOR is a module-private static initialized exactly
    // once per generation, before the firmware can observe it.
    unsafe {
        let app = core::ptr::addr_of_mut!(APP_DESCRIPTOR).cast::<launcher_app_descriptor>();
        app.write_bytes(0, 1);
        (*app).struct_size = core::mem::size_of::<launcher_app_descriptor>() as u32;
        (*app).launcher_icon_resource = LAUNCHER_ICON.as_ptr() as *mut core::ffi::c_void;
        (*app).app_id = APP_ID;
        (*app).launcher_metadata_callback =
            launcher_display_name as *const () as *mut core::ffi::c_void;
    }
}

#[cfg(feature = "device")]
fn descriptor_init(index: usize, name: &[u8], page_id: u16) {
    let descriptor = page_descriptor_ptr(index);
    // SAFETY: descriptor points at a zero-valid region of the static array;
    // the firmware copies these fields during app_install.
    unsafe {
        core::ptr::write_bytes(descriptor.cast::<u8>(), 0, core::mem::size_of::<firmware_page_descriptor>());
        (*descriptor).page_name = name.as_ptr() as *mut core::ffi::c_void;
        (*descriptor).page_id = page_id;
        (*descriptor).app_id = APP_ID;
        (*descriptor).on_signal = page_on_signal as *const () as *mut core::ffi::c_void;
        (*descriptor).on_create = page_on_create as *const () as *mut core::ffi::c_void;
        (*descriptor).on_resume = page_on_resume as *const () as *mut core::ffi::c_void;
        (*descriptor).on_pause = page_on_pause as *const () as *mut core::ffi::c_void;
        (*descriptor).on_destroy = page_on_destroy as *const () as *mut core::ffi::c_void;
    }
}

/// Executes one native-app publication stage. Stage 1 registers the app and
/// page, stage 2 adds the Launcher entry after miwear processed the
/// app-registry event.
#[cfg(feature = "device")]
pub fn install_stage(stage: u32) -> Result<(), i32> {
    let existing = unsafe { app_lookup(APP_ID) };

    if stage == 1 {
        if !existing.is_null() {
            // Already registered in a previous boot-generation: verify the
            // package field via the exact target descriptor layout.
            let package: *const u8 = unsafe {
                core::ptr::read(existing.cast::<u8>().add(APP_PACKAGE_OFFSET) as *const *const u8)
            };
            if !c_str_equal(package, PACKAGE_NAME) {
                return Err(-101);
            }
            return Ok(());
        }

        app_descriptor_init();
        descriptor_init(PAGE_MAIN, PAGE_NAME_MAIN, PAGE_MAIN as u16);

        let pages: [*mut firmware_page_descriptor; PAGE_COUNT] = [page_descriptor_ptr(0)];
        let install_result = unsafe {
            app_install(
                core::ptr::addr_of_mut!(APP_DESCRIPTOR).cast::<launcher_app_descriptor>(),
                pages.as_ptr(),
                PAGE_COUNT as u32,
            )
        };
        let _ = install_result;
        let installed = unsafe { app_lookup(APP_ID) };
        if installed.is_null() {
            return Err(-100);
        }
        return Ok(());
    }

    if stage == 2 {
        if existing.is_null() {
            return Err(-102);
        }
        // `app_launcher_add` returns an implementation-defined bookkeeping
        // result; the lookup above is the validity gate (same reasoning as the
        // reference module).
        let _launcher_result = unsafe { launcher_add(APP_ID) };
        return Ok(());
    }

    Err(-103)
}

#[cfg(not(feature = "device"))]
pub fn install_stage(stage: u32) -> Result<(), i32> {
    match stage {
        1 | 2 => Ok(()),
        _ => Err(-103),
    }
}

// ---------------------------------------------------------------------------
// Page lifecycle (firmware -> module). All rendering happens on the page
// owner thread; the pomodoro state machine lives in `pomodoro.rs`.
// ---------------------------------------------------------------------------

#[cfg(feature = "device")]
extern "C" fn page_on_signal(
    _page: *mut firmware_page_descriptor,
    _event: u32,
    _payload: *mut core::ffi::c_void,
) -> i32 {
    0
}

#[cfg(feature = "device")]
extern "C" fn page_on_create(
    page: *mut firmware_page_descriptor,
    root: *mut core::ffi::c_void,
    _start_data: *mut core::ffi::c_void,
) -> i32 {
    let index = page_id_of(page);
    if index >= PAGE_COUNT {
        return -1;
    }
    pomodoro::page_create(root)
}

#[cfg(feature = "device")]
extern "C" fn page_on_resume(page: *mut firmware_page_descriptor) -> i32 {
    if page_id_of(page) >= PAGE_COUNT {
        return -1;
    }
    pomodoro::page_resume()
}

#[cfg(feature = "device")]
extern "C" fn page_on_pause(page: *mut firmware_page_descriptor) -> i32 {
    if page_id_of(page) >= PAGE_COUNT {
        return -1;
    }
    pomodoro::page_pause()
}

#[cfg(feature = "device")]
extern "C" fn page_on_destroy(page: *mut firmware_page_descriptor) -> i32 {
    if page_id_of(page) >= PAGE_COUNT {
        return -1;
    }
    pomodoro::page_destroy()
}

#[cfg(feature = "device")]
fn page_id_of(page: *mut firmware_page_descriptor) -> usize {
    if page.is_null() {
        return usize::MAX;
    }
    // SAFETY: the firmware passes one of the descriptors we registered.
    usize::from(unsafe { (*page).page_id })
}

// Status word consumed by `canopus_mod_query`.
pub static POMODORO_STATE: AtomicU32 = AtomicU32::new(0);

pub fn snapshot() -> u32 {
    POMODORO_STATE.load(Ordering::Acquire)
}
