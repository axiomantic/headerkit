"""Tests for diff.py abi_mode=True opaque struct tag/typedef stem and C enum vs Nim int32_t parity."""

from __future__ import annotations

from headerkit import get_backend
from headerkit.ir import (
    CType,
    Enum,
    EnumValue,
    Function,
    Header,
    Parameter,
    Pointer,
    Struct,
    Typedef,
)
from headerkit.transforms.nim_cleanup import NimCleanupTransform
from headerkit.writers.diff import DiffEntry, diff_headers

_BASELINE_LOCKFREE_C = """\
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

typedef enum lf_status {
    LF_ERR_PANIC = -2,
    LF_ERR_FAILURE = -1,
    LF_OK = 0,
    LF_ERR_EMPTY = 1,
    LF_ERR_FULL = 2,
    LF_ERR_CLOSED = 3,
    LF_ERR_INVALID_ARG = 4,
    LF_ERR_REGISTRY_FULL = 5,
    LF_ERR_UNSUPPORTED = 6
} lf_status_t;

typedef enum lf_overflow_mode {
    LF_OVERFLOW_DROP_OLDEST = 0,
    LF_OVERFLOW_BACKOFF = 1
} lf_overflow_mode_t;

typedef void (*lf_item_destructor_fn)(void* item, void* user_data);

typedef struct lf_queue lf_queue_t;
typedef struct lf_producer lf_producer_t;
typedef struct lf_consumer lf_consumer_t;

lf_status_t lf_unbounded_mpmc_create(
    size_t segment_size,
    size_t max_threads,
    lf_item_destructor_fn destructor,
    void* user_data,
    lf_queue_t** out_queue
);

lf_status_t lf_queue_close(lf_queue_t* queue);
lf_status_t lf_queue_destroy(lf_queue_t* queue);
lf_status_t lf_producer_acquire(lf_queue_t* queue, lf_producer_t** out_prod);
lf_status_t lf_consumer_acquire(lf_queue_t* queue, lf_consumer_t** out_cons);
lf_status_t lf_push_ex(lf_producer_t* prod, void* item, bool* out_was_empty);
size_t lf_queue_len(const lf_queue_t* queue);
bool lf_queue_is_empty(const lf_queue_t* queue);
"""

_TARGET_NIM_HEADER_C = """\
#include "nimbase.h"

typedef NI32 tyEnum_lf_status_t__a1b2c3d4;
typedef NI32 tyEnum_lf_overflow_mode_t__e5f6a7b8;
typedef struct lf_queue_t lf_queue_t;
typedef struct lf_producer_t lf_producer_t;
typedef struct lf_consumer_t lf_consumer_t;
typedef N_CDECL_PTR(void, tyProc__itemDestructor)(void* item_p0, void* userData_p1);

N_LIB_EXPORT N_CDECL(tyEnum_lf_status_t__a1b2c3d4, lf_unbounded_mpmc_create)(
    NU segment_size_p0,
    NU max_threads_p1,
    tyProc__itemDestructor destructor_p2,
    void* user_data_p3,
    lf_queue_t** out_queue_p4
);

N_LIB_EXPORT N_CDECL(tyEnum_lf_status_t__a1b2c3d4, lf_queue_close)(lf_queue_t* queue_p0);
N_LIB_EXPORT N_CDECL(tyEnum_lf_status_t__a1b2c3d4, lf_queue_destroy)(lf_queue_t* queue_p0);
N_LIB_EXPORT N_CDECL(tyEnum_lf_status_t__a1b2c3d4, lf_producer_acquire)(lf_queue_t* queue_p0, lf_producer_t** out_prod_p1);
N_LIB_EXPORT N_CDECL(tyEnum_lf_status_t__a1b2c3d4, lf_consumer_acquire)(lf_queue_t* queue_p0, lf_consumer_t** out_cons_p1);
N_LIB_EXPORT N_CDECL(tyEnum_lf_status_t__a1b2c3d4, lf_push_ex)(lf_producer_t* prod_p0, void* item_p1, NIM_BOOL* out_was_empty_p2);
N_LIB_EXPORT N_CDECL(NU, lf_queue_len)(lf_queue_t* queue_p0);
N_LIB_EXPORT N_CDECL(NIM_BOOL, lf_queue_is_empty)(lf_queue_t* queue_p0);
N_LIB_EXPORT N_CDECL(void, NimMain)(void);
"""


def test_opaque_struct_tag_vs_typedef_stem_matches_in_abi_mode() -> None:
    """Verify `typedef struct lf_queue lf_queue_t;` vs `lf_queue_t` without separate tag has 0 signature changes."""
    baseline = Header(
        path="lockfree.h",
        declarations=[
            Struct("lf_queue", fields=[]),
            Typedef("lf_queue_t", CType("struct lf_queue")),
            Function(
                "lf_queue_close",
                CType("int32_t"),
                [Parameter("queue", Pointer(CType("lf_queue_t")))],
            ),
            Function(
                "lf_queue_create",
                CType("int32_t"),
                [Parameter("out_queue", Pointer(Pointer(CType("lf_queue_t"))))],
            ),
        ],
    )
    target = Header(
        path="cabi.h",
        declarations=[
            Struct("lf_queue_t", fields=[]),
            Typedef("lf_queue_t", CType("struct lf_queue_t")),
            Function(
                "lf_queue_close",
                CType("int32_t"),
                [Parameter("queue_p0", Pointer(CType("lf_queue_t")))],
            ),
            Function(
                "lf_queue_create",
                CType("int32_t"),
                [Parameter("out_queue_p0", Pointer(Pointer(CType("lf_queue_t"))))],
            ),
        ],
    )

    report = diff_headers(baseline, target, abi_mode=True)
    sig_changes = [e for e in report.entries if e.kind == "function_signature_changed"]
    assert sig_changes == []
    assert report.breaking_count == 0


def test_c_enum_vs_nim_int32_t_matches_in_abi_mode() -> None:
    """Verify `typedef enum lf_status { ... } lf_status_t;` matches Nim `typedef int32_t lf_status_t;` in ABI mode."""
    baseline = Header(
        path="lockfree.h",
        declarations=[
            Enum(
                "lf_status",
                [
                    EnumValue("LF_ERR_FAILURE", -1),
                    EnumValue("LF_OK", 0),
                ],
            ),
            Typedef("lf_status_t", CType("enum lf_status")),
            Function(
                "lf_queue_close",
                CType("lf_status_t"),
                [Parameter("mode", CType("lf_status_t"))],
            ),
        ],
    )
    target = Header(
        path="cabi.h",
        declarations=[
            Typedef("lf_status_t", CType("int32_t")),
            Function(
                "lf_queue_close",
                CType("lf_status_t"),
                [Parameter("mode_p0", CType("lf_status_t"))],
            ),
            Function("NimMain", CType("void"), []),
        ],
    )

    report = diff_headers(baseline, target, abi_mode=True)
    sig_changes = [e for e in report.entries if e.kind == "function_signature_changed"]
    assert sig_changes == []
    assert report.breaking_count == 0


def test_lockfree_header_vs_nim_cleanup_pipeline_zero_false_positives() -> None:
    """End-to-end parse + NimCleanupTransform + diff_headers(abi_mode=True) on lockfree C ABI."""
    backend = get_backend("tree-sitter")
    baseline_ir = backend.parse(_BASELINE_LOCKFREE_C, "lockfree.h")
    raw_target_ir = backend.parse(_TARGET_NIM_HEADER_C, "cabi.h")
    target_ir = NimCleanupTransform().apply(raw_target_ir)

    report = diff_headers(baseline_ir, target_ir, abi_mode=True)
    sig_changes = [e for e in report.entries if e.kind == "function_signature_changed"]
    assert sig_changes == []
    assert report.breaking_count == 0


def test_negative_controls_distinct_opaque_handles_and_64bit_return_flagged() -> None:
    """Verify distinct opaque handle types (`lf_queue_t**` vs `lf_stack_t**`) and 64-bit return mismatch still fail."""
    baseline = Header(
        path="lockfree.h",
        declarations=[
            Enum("lf_status", [EnumValue("LF_OK", 0)]),
            Typedef("lf_status_t", CType("enum lf_status")),
            Struct("lf_queue", fields=[]),
            Typedef("lf_queue_t", CType("struct lf_queue")),
            Struct("lf_stack", fields=[]),
            Typedef("lf_stack_t", CType("struct lf_stack")),
            Function(
                "lf_create",
                CType("lf_status_t"),
                [Parameter("out_q", Pointer(Pointer(CType("lf_queue_t"))))],
            ),
        ],
    )
    target = Header(
        path="cabi.h",
        declarations=[
            Typedef("lf_status_t", CType("int64_t")),
            Struct("lf_stack_t", fields=[]),
            Function(
                "lf_create",
                CType("lf_status_t"),
                [Parameter("out_q_p0", Pointer(Pointer(CType("lf_stack_t"))))],
            ),
        ],
    )

    report = diff_headers(baseline, target, abi_mode=True)
    sig_changes = [e for e in report.entries if e.kind == "function_signature_changed"]
    assert sig_changes == [
        DiffEntry(
            kind="function_signature_changed",
            severity="breaking",
            name="lf_create",
            detail="return type changed from 'lf_status_t' to 'lf_status_t'",
            baseline="lf_status_t",
            target="lf_status_t",
        ),
        DiffEntry(
            kind="function_signature_changed",
            severity="breaking",
            name="lf_create",
            detail="parameter 0 type changed from 'lf_queue_t**' to 'lf_stack_t**'",
            baseline="lf_queue_t**",
            target="lf_stack_t**",
        ),
    ]
