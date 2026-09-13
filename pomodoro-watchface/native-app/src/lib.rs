//! Pomodoro (番茄钟) Canopus native module — Mi Band 9 Pro / Vela 3.1.175 target.
//!
//! Modeled directly on Searchstars/Canopus-Module-BluetoothAudio:
//!
//!   canopus_module_descriptor  -> ModuleDescriptorV1 with FLAG_HAS_NATIVE_APP
//!   publish_native_app_stage(1) -> app_install() + page descriptors
//!   publish_native_app_stage(2) -> launcher_add(APP_ID)
//!
//! All firmware symbols (`app_install`, `launcher_add`, the
//! `launcher_app_descriptor` / `firmware_page_descriptor` layouts, offsets such
//! as `APP_PACKAGE_OFFSET`, and the vibrator/notification entry points) come
//! from the per-target **private ABI backend** (`canopus_target_private`), i.e.
//! the `device` feature. The device build takes those from that crate; the host
//! build uses the local ABI mirror in `abi.rs`, so `cargo test` runs with no
//! framework present.
//!
//! Be clear about what exists: the upstream Canopus framework ships target
//! packs only for Band 10 Pro `3.101.036`/`.043` and Band 11 `4.100.139` — not
//! for this device. The addresses this module needs are recovered by our own
//! `tools/target_pack.py` into `targets/xiaomi-band-9-pro-3.1.175.{md,json}`,
//! and `app_lookup` is still unresolved there (no such string in the image).
//! The bigger gap is the loader, not the tables: see
//! `../docs/userland-loader-blueprint.md`.
#![cfg_attr(not(test), no_std)]

mod abi;
// The host build exists to run the state machine under `cargo test`; without
// the page framework nothing calls into these modules, so silence the
// resulting dead-code noise there instead of sprinkling `#[allow]`s.
#[cfg_attr(not(feature = "device"), allow(dead_code))]
mod native_app;
#[cfg_attr(not(feature = "device"), allow(dead_code))]
mod pomodoro;

use abi::*;
use core::sync::atomic::{AtomicBool, AtomicU32, Ordering};

const MAGIC: u32 = 0x504F_4D31; // "POM1"

static ACTIVE: AtomicBool = AtomicBool::new(false);
static RESIDENT: AtomicBool = AtomicBool::new(false);
static LAST_ERROR: AtomicU32 = AtomicU32::new(0);

const fn pack<const N: usize>(value: &[u8]) -> [u8; N] {
    let mut out = [0; N];
    let mut i = 0;
    while i < value.len() && i < N {
        out[i] = value[i];
        i += 1;
    }
    out
}

#[cfg(feature = "device")]
const MODULE_TARGET_ID: &[u8] = canopus_target_private::TARGET_ID.as_bytes();
#[cfg(not(feature = "device"))]
const MODULE_TARGET_ID: &[u8] = b"host-test";

#[cfg(feature = "device")]
#[repr(C)]
struct ModuleRegistrationV1 {
    magic: u32,
    descriptor: u32,
    module_id: [u8; 32],
}

#[cfg(feature = "device")]
const REGISTRATION_MAGIC: u32 = 0x3152_4d43; // "CMR1"
#[cfg(feature = "device")]
const CANOPUS_DEVICE_PATH: &[u8] = b"/dev/canopus\0";

#[unsafe(no_mangle)]
pub extern "C" fn canopus_mod_prepare(_ctx: *const ContextV1) -> i32 {
    ACTIVE.store(false, Ordering::Release);
    RESIDENT.store(false, Ordering::Release);
    LAST_ERROR.store(0, Ordering::Release);
    0
}

#[unsafe(no_mangle)]
pub extern "C" fn canopus_mod_activate(_ctx: *const ContextV1) -> i32 {
    // Pomodoro keeps no transport state, so unlike the audio module we could
    // support unload; resident-after-activation is still the safer contract
    // because the launcher entry persists until reboot.
    RESIDENT.store(true, Ordering::Release);
    ACTIVE.store(true, Ordering::Release);
    0
}

#[unsafe(no_mangle)]
pub extern "C" fn canopus_mod_deactivate(_ctx: *const ContextV1) -> i32 {
    if RESIDENT.load(Ordering::Acquire) {
        return RESULT_REBOOT_REQUIRED as i32;
    }
    ACTIVE.store(false, Ordering::Release);
    0
}

#[unsafe(no_mangle)]
pub extern "C" fn canopus_mod_stop(ctx: *const ContextV1) -> i32 {
    canopus_mod_deactivate(ctx)
}

#[allow(clippy::not_unsafe_ptr_arg_deref)]
#[unsafe(no_mangle)]
pub extern "C" fn canopus_mod_query(writer: *mut StatusWriterV1) -> i32 {
    if writer.is_null() {
        return -1;
    }
    let writer = unsafe { &mut *writer };
    unsafe {
        if !status_put_u32(writer, MAGIC)
            || !status_put_u32(writer, ACTIVE.load(Ordering::Acquire) as u32)
            || !status_put_u32(writer, RESIDENT.load(Ordering::Acquire) as u32)
            || !status_put_u32(writer, LAST_ERROR.load(Ordering::Acquire))
            || !status_put_u32(writer, pomodoro::snapshot())
        {
            return -1;
        }
    }
    status_writer_publish(writer);
    0
}

#[unsafe(no_mangle)]
pub extern "C" fn canopus_mod_publish_native_app(_ctx: *const ContextV1) -> i32 {
    -103 // stageless publication is not used; the manager calls the staged API
}

#[unsafe(no_mangle)]
pub extern "C" fn canopus_mod_publish_native_app_stage(_ctx: *const ContextV1, stage: u32) -> i32 {
    #[cfg(feature = "device")]
    let rc = match native_app::install_stage(stage) {
        Ok(()) => 0,
        Err(error) => error,
    };
    #[cfg(not(feature = "device"))]
    let rc = if stage == 1 || stage == 2 { 0 } else { -103 };
    if rc != 0 {
        LAST_ERROR.store(rc as u32, Ordering::Release);
    }
    rc
}

#[unsafe(no_mangle)]
pub static canopus_module_descriptor: ModuleDescriptorV1 = ModuleDescriptorV1 {
    struct_size: core::mem::size_of::<ModuleDescriptorV1>() as u32,
    abi_major: ABI_MAJOR,
    abi_minor: ABI_MINOR,
    flags: FLAG_HAS_NATIVE_APP
        | FLAG_NATIVE_APP_STANDALONE
        | FLAG_REGISTERS_LAUNCHER_ENTRY
        | FLAG_REQUIRES_UI_DISPATCHER
        | FLAG_APP_UNREGISTER_REBOOT_REQUIRED,
    module_id: pack(b"pomodoro"),
    module_version: pack(b"0.1.0"),
    build_id: pack(b"pomodoro-0.1.0"),
    target_id: pack(MODULE_TARGET_ID),
    prepare: Some(canopus_mod_prepare),
    activate: Some(canopus_mod_activate),
    deactivate: Some(canopus_mod_deactivate),
    stop: Some(canopus_mod_stop),
    query: Some(canopus_mod_query),
    publish_native_app: Some(canopus_mod_publish_native_app),
    publish_native_app_stage: Some(canopus_mod_publish_native_app_stage),
};

#[cfg(feature = "device")]
#[unsafe(no_mangle)]
pub extern "C" fn canopus_register_module_descriptor() -> i32 {
    if canopus_target_private::canopus_identity_guard() != 0 {
        return -1;
    }
    let registration = ModuleRegistrationV1 {
        magic: REGISTRATION_MAGIC,
        descriptor: core::ptr::addr_of!(canopus_module_descriptor) as usize as u32,
        module_id: pack(b"pomodoro"),
    };
    let fd = unsafe { canopus_target_private::nuttx_open(CANOPUS_DEVICE_PATH.as_ptr(), 2) };
    if fd < 0 {
        return fd;
    }
    let written = unsafe {
        canopus_target_private::nuttx_write(
            fd,
            core::ptr::addr_of!(registration).cast(),
            core::mem::size_of::<ModuleRegistrationV1>() as u32,
        )
    };
    let close = unsafe { canopus_target_private::nuttx_close(fd) };
    if written != core::mem::size_of::<ModuleRegistrationV1>() as i32 {
        return if written < 0 { written } else { -1 };
    }
    close
}

#[unsafe(no_mangle)]
pub extern "C" fn canopus_module_descriptor_ptr() -> *const ModuleDescriptorV1 {
    &canopus_module_descriptor
}

#[cfg(not(test))]
#[panic_handler]
fn canopus_panic(_info: &core::panic::PanicInfo) -> ! {
    // A module must never unwind into the supervisor's frame.
    loop {
        core::hint::spin_loop();
    }
}
