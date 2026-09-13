//! ABI v1 surface for a Canopus-style native module.
//!
//! Two build modes:
//!
//! * `device` — re-export the real definitions from the private framework
//!   crate `canopus_target_private`. That is the only arrangement in which the
//!   resulting object file matches the supervisor's expectation, so nothing here
//!   is allowed to drift from it.
//! * host (default) — local definitions so `cargo test` can exercise the state
//!   machine with no framework present. They are intentionally a faithful
//!   mirror of *our* module-side contract (fixed-width fields, `struct_size`
//!   first, no pointers in stored data), not a guess at private internals.
//!
//! Field order is frozen: ABI v1 additions append fields and never move
//! existing ones.

#[cfg(feature = "device")]
pub use canopus_target_private::{
    status_put_u32, status_writer_publish, ContextV1, ModuleDescriptorV1, StatusWriterV1,
    ABI_MAJOR, ABI_MINOR, FLAG_APP_UNREGISTER_REBOOT_REQUIRED, FLAG_HAS_NATIVE_APP,
    FLAG_NATIVE_APP_STANDALONE, FLAG_REGISTERS_LAUNCHER_ENTRY, FLAG_REQUIRES_UI_DISPATCHER,
    RESULT_REBOOT_REQUIRED,
};

#[cfg(not(feature = "device"))]
mod host {
    //! Host-only mirror of the ABI, used by `cargo test`.

    pub const ABI_MAJOR: u32 = 1;
    pub const ABI_MINOR: u32 = 0;

    pub const FLAG_HAS_NATIVE_APP: u32 = 1 << 0;
    pub const FLAG_NATIVE_APP_STANDALONE: u32 = 1 << 1;
    pub const FLAG_REGISTERS_LAUNCHER_ENTRY: u32 = 1 << 2;
    pub const FLAG_REQUIRES_UI_DISPATCHER: u32 = 1 << 3;
    pub const FLAG_APP_UNREGISTER_REBOOT_REQUIRED: u32 = 1 << 4;

    /// Deactivation is refused while the app's launcher entry is live.
    pub const RESULT_REBOOT_REQUIRED: i32 = -38;

    /// What the framework hands a lifecycle callback.
    #[repr(C)]
    pub struct ContextV1 {
        pub struct_size: u32,
        pub abi_major: u32,
        pub abi_minor: u32,
        pub module_arena: *mut core::ffi::c_void,
        pub module_arena_size: u32,
        pub firmware_identity: u32,
    }

    /// Opaque append-only status buffer. The framework owns the storage and
    /// reads it after `canopus_mod_query` returns; the module only appends
    /// fixed-width words, which is why there are no pointers in it.
    #[repr(C)]
    pub struct StatusWriterV1 {
        pub struct_size: u32,
        pub capacity: u32,
        pub written: u32,
        pub flags: u32,
    }

    /// Appends one `u32`. Returns `false` when the buffer is full.
    ///
    /// # Safety
    /// `writer` must point to a `StatusWriterV1` whose `written`/`capacity`
    /// really describe the buffer the framework allocated.
    pub unsafe fn status_put_u32(writer: *mut StatusWriterV1, value: u32) -> bool {
        if writer.is_null() {
            return false;
        }
        // SAFETY: caller contract, plus a capacity check before any store.
        let w = &mut *writer;
        if w.written + 4 > w.capacity {
            return false;
        }
        // The payload follows the header in the framework's buffer.
        let payload = (writer as *mut u8).add(core::mem::size_of::<StatusWriterV1>());
        core::ptr::write_unaligned(payload.add(w.written as usize).cast::<u32>(), value);
        w.written += 4;
        true
    }

    /// Publishes the appended words so the framework sees a coherent snapshot.
    ///
    /// # Safety
    /// Same contract as [`status_put_u32`].
    pub unsafe fn status_writer_publish(writer: *mut StatusWriterV1) {
        if writer.is_null() {
            return;
        }
        let w = &mut *writer;
        w.flags |= 1; // STATUS_PUBLISHED
    }

    pub type LifecycleFn = Option<extern "C" fn(*const ContextV1) -> i32>;
    pub type QueryFn = Option<extern "C" fn(*mut StatusWriterV1) -> i32>;
    pub type StageFn = Option<extern "C" fn(*const ContextV1, u32) -> i32>;

    /// `canopus_module_descriptor` — the one symbol the supervisor looks up.
    #[repr(C)]
    pub struct ModuleDescriptorV1 {
        pub struct_size: u32,
        pub abi_major: u32,
        pub abi_minor: u32,
        pub flags: u32,
        pub module_id: [u8; 32],
        pub module_version: [u8; 32],
        pub build_id: [u8; 32],
        pub target_id: [u8; 32],
        pub prepare: LifecycleFn,
        pub activate: LifecycleFn,
        pub deactivate: LifecycleFn,
        pub stop: LifecycleFn,
        pub query: QueryFn,
        pub publish_native_app: LifecycleFn,
        pub publish_native_app_stage: StageFn,
    }
}

#[cfg(not(feature = "device"))]
pub use host::*;
