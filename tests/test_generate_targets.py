"""Tests for generate() transforms pipeline, cache invalidation, and [[targets]] multi-target execution."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from headerkit._cache_key import compute_output_cache_key
from headerkit._cli import main
from headerkit._config import TargetConfig, load_config
from headerkit._generate import generate, generate_all, resolve_transforms
from headerkit.ir import CType, Function, Header, Parameter
from headerkit.transforms import (
    FilterByHeader,
    FilterBySymbol,
    FilterDeprecated,
    NimCleanupTransform,
    StripPaddingFields,
    StripPrefix,
)


class TestResolveTransforms:
    """Unit tests for resolve_transforms() shorthand, dict, ChainedFilter, and negative controls."""

    def test_resolve_pipeline_filter_and_chained_filter(self) -> None:
        f1 = FilterBySymbol(include=["foo_*"])
        f2 = FilterDeprecated()
        chained = f1 >> f2

        resolved = resolve_transforms([chained, StripPaddingFields()])
        assert len(resolved) == 3
        assert resolved[0] is f1
        assert resolved[1] is f2
        assert isinstance(resolved[2], StripPaddingFields)

    def test_resolve_string_shorthands_and_params(self) -> None:
        resolved = resolve_transforms(
            [
                "nim_cleanup",
                "filter_by_symbol:include=pub_*,exclude=*_priv",
                "filter_deprecated",
                "filter_by_header:allow=api.h,deny=internal.h",
                "strip_padding",
                "strip_prefix:prefix=my_",
            ]
        )
        assert [t.cache_fingerprint() for t in resolved] == [
            NimCleanupTransform().cache_fingerprint(),
            FilterBySymbol(include=["pub_*"], exclude=["*_priv"]).cache_fingerprint(),
            FilterDeprecated().cache_fingerprint(),
            FilterByHeader(allow=["api.h"], deny=["internal.h"]).cache_fingerprint(),
            StripPaddingFields().cache_fingerprint(),
            StripPrefix(prefix="my_").cache_fingerprint(),
        ]

    def test_resolve_dict_specs(self) -> None:
        resolved = resolve_transforms(
            [
                {"name": "filter_by_symbol", "include": ["api_*"], "exclude": ["api_internal_*"]},
                {"name": "nim_cleanup", "keep_nim_main": True},
            ]
        )
        assert [t.cache_fingerprint() for t in resolved] == [
            FilterBySymbol(include=["api_*"], exclude=["api_internal_*"]).cache_fingerprint(),
            NimCleanupTransform(keep_nim_main=True).cache_fingerprint(),
        ]

    def test_negative_controls_invalid_transform_specs(self) -> None:
        with pytest.raises(ValueError, match="Unknown transform: 'nonexistent_filter'"):
            resolve_transforms(["nonexistent_filter"])

        with pytest.raises(ValueError, match="Malformed transform argument"):
            resolve_transforms(["filter_by_symbol:missing_equals"])

        with pytest.raises(ValueError, match="Transform mapping must specify a string 'name' key"):
            resolve_transforms([{"include": ["foo_*"]}])

        with pytest.raises(TypeError, match="Invalid transform specification"):
            resolve_transforms([12345])  # type: ignore[list-item]


class TestGenerateTransformsAndCacheInvalidation:
    """Tests for generate(..., transforms=...) and output cache key invalidation."""

    def test_compute_output_cache_key_changes_with_transforms(self) -> None:
        key_base = compute_output_cache_key(ir_cache_key="ir123", writer_name="json")
        key_f1 = compute_output_cache_key(
            ir_cache_key="ir123",
            writer_name="json",
            transforms_fingerprint=[FilterBySymbol(include=["foo_*"]).cache_fingerprint()],
        )
        key_f2 = compute_output_cache_key(
            ir_cache_key="ir123",
            writer_name="json",
            transforms_fingerprint=[FilterBySymbol(include=["bar_*"]).cache_fingerprint()],
        )
        assert key_base != key_f1
        assert key_f1 != key_f2

    def test_generate_output_cache_invalidates_when_transforms_change(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        store_dir = tmp_path / ".headerkit"
        hdr = tmp_path / "sample.h"
        hdr.write_text("int keep_me(void);\nint _drop_me(void);\n", encoding="utf-8")

        fake_ir = Header(
            path=str(hdr),
            declarations=[
                Function(name="keep_me", return_type=CType("int"), parameters=[]),
                Function(name="_drop_me", return_type=CType("int"), parameters=[]),
            ],
        )

        class DummyBackend:
            name = "dummy"

            def parse(
                self, code: str, path: str, include_dirs: object = None, extra_args: object = None, **kw: object
            ) -> Header:
                return Header(path=str(hdr), declarations=list(fake_ir.declarations))

        with patch("headerkit._generate.get_backend", return_value=DummyBackend()):
            out_unfiltered = generate(
                header_path=hdr,
                writer_name="json",
                backend_name="dummy",
                store_dir=store_dir,
            )
            out_filtered = generate(
                header_path=hdr,
                writer_name="json",
                backend_name="dummy",
                transforms=[FilterBySymbol(exclude=["_*"])],
                store_dir=store_dir,
            )

        names_unfiltered = [d["name"] for d in json.loads(out_unfiltered)["declarations"]]
        names_filtered = [d["name"] for d in json.loads(out_filtered)["declarations"]]
        assert names_unfiltered == ["keep_me", "_drop_me"]
        assert names_filtered == ["keep_me"]


class TestTargetsMultiTargetExecution:
    """Tests for [[targets]] config loading and single-parse multi-target execution."""

    def test_load_config_parses_targets_and_inherits_defaults(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "headerkit.toml"
        cfg_file.write_text(
            textwrap.dedent("""\
                backend = "libclang"
                headers = ["include/api.h"]
                defines = ["GLOBAL_DEF=1"]
                include_dirs = ["include"]
                transforms = ["strip_padding"]

                [writer.cffi]
                exclude_patterns = ["^_"]

                [[targets]]
                name = "cffi_out"
                writer = "cffi"
                output = "dist/api_cffi.txt"

                [[targets]]
                name = "json_filtered"
                writer = "json"
                output = "dist/api.json"
                defines = ["CUSTOM_DEF=2"]
                transforms = [{name = "filter_by_symbol", include = ["api_*"]}]
            """),
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        assert len(cfg.targets) == 2

        t0 = cfg.targets[0]
        assert t0 == TargetConfig(
            name="cffi_out",
            writer="cffi",
            writers=["cffi"],
            output="dist/api_cffi.txt",
            headers=["include/api.h"],
            defines=["GLOBAL_DEF=1"],
            include_dirs=["include"],
            backend="libclang",
            transforms=["strip_padding"],
            options={"exclude_patterns": ["^_"]},
            target=None,
            project_prefixes=[],
            recursive_includes=True,
            max_depth=None,
            use_default_includes=True,
            extra_args=[],
        )

        t1 = cfg.targets[1]
        assert t1.name == "json_filtered"
        assert t1.writer == "json"
        assert t1.defines == ["CUSTOM_DEF=2"]
        assert t1.transforms == [{"name": "filter_by_symbol", "include": ["api_*"]}]

    def test_generate_targets_parses_once_per_unique_input_and_isolates_transforms(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        hdr = tmp_path / "api.h"
        hdr.write_text("int api_init(void);\nint _internal_helper(void);\n", encoding="utf-8")

        parse_calls: list[str] = []
        shared_declarations = [
            Function(
                name="api_init",
                return_type=CType("int"),
                parameters=[Parameter(name="count", type=CType("int"))],
            ),
            Function(
                name="_internal_helper",
                return_type=CType("int"),
                parameters=[],
            ),
        ]

        class CountingBackend:
            name = "counting"

            def parse(
                self, code: str, path: str, include_dirs: object = None, extra_args: object = None, **kw: object
            ) -> Header:
                parse_calls.append(path)
                return Header(path=path, declarations=list(shared_declarations))

        out_filtered_json = tmp_path / "out" / "filtered.json"
        out_full_json = tmp_path / "out" / "full.json"
        out_pyi = tmp_path / "out" / "api.pyi"

        targets = [
            TargetConfig(
                name="filtered_json",
                writer="json",
                output=str(out_filtered_json),
                headers=[str(hdr)],
                backend="counting",
                transforms=[{"name": "filter_by_symbol", "exclude": ["_*"]}],
            ),
            TargetConfig(
                name="full_json",
                writer="json",
                output=str(out_full_json),
                headers=[str(hdr)],
                backend="counting",
                transforms=[],
            ),
            TargetConfig(
                name="pyi_stubs",
                writer="pyi",
                output=str(out_pyi),
                headers=[str(hdr)],
                backend="counting",
                transforms=["filter_by_symbol:exclude=_*"],
            ),
        ]

        with patch("headerkit._generate.get_backend", return_value=CountingBackend()):
            results = generate_all(
                targets=targets,
                no_cache=True,
                project_root=tmp_path,
            )

        # Layer-1 parse MUST run only ONCE across all 3 targets sharing identical parse inputs
        assert len(parse_calls) == 1
        assert len(results) == 3

        # Target 1 filtered out _internal_helper
        filtered_data = json.loads(out_filtered_json.read_text(encoding="utf-8"))
        assert [d["name"] for d in filtered_data["declarations"]] == ["api_init"]

        # Target 2 received the full unmutated IR (proving Target 1 did not mutate Layer-1 IR in place)
        full_data = json.loads(out_full_json.read_text(encoding="utf-8"))
        assert [d["name"] for d in full_data["declarations"]] == ["api_init", "_internal_helper"]

        # Target 3 wrote filtered .pyi stubs
        pyi_text = out_pyi.read_text(encoding="utf-8")
        assert "def api_init(" in pyi_text
        assert "_internal_helper" not in pyi_text

    def test_cli_executes_targets_from_headerkit_toml_and_generate_subcommand(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".git").mkdir()
        hdr = tmp_path / "lib.h"
        hdr.write_text("int pub_fn(void);\nint _priv_fn(void);\n", encoding="utf-8")

        cfg_file = tmp_path / "headerkit.toml"
        cfg_file.write_text(
            textwrap.dedent("""\
                headers = ["lib.h"]
                backend = "counting"

                [[targets]]
                name = "public_only"
                writer = "json"
                output = "build/public.json"
                transforms = ["filter_by_symbol:exclude=_*"]

                [[targets]]
                name = "all_symbols"
                writer = "json"
                output = "build/all.json"
            """),
            encoding="utf-8",
        )

        class DummyBackend:
            name = "counting"

            def parse(
                self, code: str, path: str, include_dirs: object = None, extra_args: object = None, **kw: object
            ) -> Header:
                return Header(
                    path=path,
                    declarations=[
                        Function(name="pub_fn", return_type=CType("int"), parameters=[]),
                        Function(name="_priv_fn", return_type=CType("int"), parameters=[]),
                    ],
                )

        monkeypatch.chdir(tmp_path)
        with patch("headerkit._generate.get_backend", return_value=DummyBackend()):
            rc = main(["generate", "--no-cache"])

        assert rc == 0
        pub_decls = [d["name"] for d in json.loads((tmp_path / "build" / "public.json").read_text())["declarations"]]
        all_decls = [d["name"] for d in json.loads((tmp_path / "build" / "all.json").read_text())["declarations"]]
        assert pub_decls == ["pub_fn"]
        assert all_decls == ["pub_fn", "_priv_fn"]
