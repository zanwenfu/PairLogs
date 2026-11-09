"""The redaction, on hand-made text: what is masked, what is left, and that JSON stays JSON.

Findings are placed by their content, never by the scan's columns (gitleaks counts columns from the start of the
100 KB piece of a file it was reading). These tests give columns that are deliberately wrong to show they are unused.
"""

import hashlib
import json

from collect import scan  # noqa: E402
from collect.scan import MASK, redact


def f(rule, secret, *, decoded=False, match=None):
    return {"rule": rule, "secret": secret, "line": 1, "end_line": 1, "start": 999, "end": 999, "decoded": decoded,
            "match": match}


def test_literal_secret_masked_everywhere():
    out, counts = redact("key=abcd1234efgh5678 and again abcd1234efgh5678", [f("generic-api-key", "abcd1234efgh5678")])
    assert out == f"key={MASK} and again {MASK}"
    assert counts["secrets"] == {"generic-api-key": 2}


def test_trailing_backslash_kept_so_json_stays_valid():
    line = json.dumps({"cmd": 'export TOKEN="s3cr3tvalue\\"'})
    out, _ = redact(line, [f("generic-api-key", "s3cr3tvalue\\")])
    assert json.loads(out)["cmd"].startswith('export TOKEN="' + MASK)


def test_short_secret_masked_only_inside_its_match():
    out, counts = redact("curl -u ann:pw1 x; the word pw1 elsewhere", [f("curl-auth-user", "pw1", match="-u ann:pw1")])
    assert out == f"curl -u ann:{MASK} x; the word pw1 elsewhere"
    assert counts["secrets"] == {"curl-auth-user": 1}


def test_decoded_base64_masked_by_its_run():
    out, counts = redact("x = aGVsbG8gc2VjcmV0dmFsdWU= y", [f("generic-api-key", "hello secretvalue", decoded=True)])
    assert out == f"x = {MASK} y" and counts["secrets"] == {"generic-api-key": 1}


def test_decoded_unicode_escapes_found_as_written():
    secret = "abc+def/ghi=123456"
    line = '{"v": "abc\\u002bdef\\/ghi\\u003d123456"}'
    out, _ = redact(line, [f("generic-api-key", secret, decoded=True)])
    assert "def" not in out and json.loads(out)["v"] == MASK


def test_multiline_private_key_written_with_json_escapes():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabcdefghij\n-----END RSA PRIVATE KEY-----"
    line = json.dumps({"content": "before " + key + " after"})
    out, _ = redact(line, [f("private-key", key, decoded=True)])
    assert json.loads(out)["content"] == f"before {MASK} after"


def test_multiline_literal_secret_replaced():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabcdefgh\n-----END RSA PRIVATE KEY-----"
    out, _ = redact("x " + key + " y", [f("private-key", key)])
    assert out == f"x {MASK} y"


def test_unplaceable_finding_raises():
    for finding in ({**f("x", "zz-not-here", decoded=True), "line": None, "end_line": None},   # no line named
                    {**f("x", "zz-not-here", decoded=True), "line": 7, "end_line": 7}):   # past the text's end
        try:
            redact("short line", [finding])
        except RuntimeError:
            continue
        raise AssertionError("a finding that cannot be placed must raise")


def test_emails_masked_but_noreply_examples_and_files_kept():
    text = ("a jane.doe@company.io b noreply@anthropic.com c git@github.com d me@example.com "
            "e 1+bob@users.noreply.github.com f logo@2x.png g icon@3x.jpg")
    out, counts = redact(text, [])
    assert "jane.doe@company.io" not in out and "1+bob@users.noreply.github.com" not in out
    for kept in ("noreply@anthropic.com", "git@github.com", "me@example.com", "logo@2x.png", "icon@3x.jpg"):
        assert kept in out, kept
    assert counts["emails"] == 2


def test_email_after_a_json_escape_keeps_the_json_valid():
    line = json.dumps({"text": "line one\njohn@corp.io and <ann@corp.io>"})
    line = line.replace("<", "\\u003c").replace(">", "\\u003e")
    out, counts = redact(line, [])
    value = json.loads(out)["text"]
    assert value == f"line one\n{MASK} and <{MASK}>" and counts["emails"] == 2


def test_decorator_after_an_escaped_newline_is_no_email():
    line = json.dumps({"code": 'x = 1\n@app.get("/")\n@pytest.mark.asyncio\ndef f(): ...'})
    out, counts = redact(line, [])
    assert out == line and counts["emails"] == 0


def test_home_paths_posix_windows_escaped_unicode_and_odd_endings():
    text = ('/Users/janedoe/proj/a.py /home/bob/x C:\\Users\\Alice Smith\\Desktop ' + json.dumps("C:\\Users\\carol\\code")
            + " /home/runner/work /Users/Shared/x /home/user/y /Users/José/p `/home/dan` /Users/eve;")
    out, counts = redact(text, [])
    for name in ("janedoe", "bob", "Alice Smith", "carol", "José", "dan", "eve"):
        assert name not in out, name
    assert "/home/runner/work" in out and "/Users/Shared/x" in out and "/home/user/y" in out
    assert counts["home_users"] == 7


def test_claude_code_dashed_project_folders():
    text = ("cwd /Users/jane.doe/proj then ~/.claude/projects/-Users-jane-doe-proj/memory and "
            "/private/tmp/claude-501/-Users-kim-app/tasks")
    out, _ = redact(text, [])
    assert "jane" not in out and "-doe-" not in out and "kim" not in out


def test_json_escaped_windows_path_stays_valid_json():
    line = json.dumps({"cwd": "C:\\Users\\carol\\code", "note": "/Users/dave/x"})
    out, counts = redact(line, [])
    value = json.loads(out)
    assert value["cwd"] == "C:\\Users\\" + MASK + "\\code" and value["note"] == "/Users/" + MASK + "/x"
    assert counts["home_users"] == 2


def test_nothing_to_mask():
    text = '{"type":"user","message":{"content":"run the tests"}}'
    out, counts = redact(text, [])
    assert out == text and counts == {"secrets": {}, "emails": 0, "home_users": 0}


# -- JSON text: a mask stays inside one string ------------------------------------------------------------------
PEM_BODY = "\\n".join("MIIEv" + "Qx7Kp2Lm9Nz4Rt6Ws8Yb3Hd5Jf1Gc" * 2 for _ in range(4))


def _private_key_finding(text: str, start: str) -> dict:
    """What gitleaks' private-key pattern reports: from the header to the next `KEY-----` at least 64 characters on."""
    a = text.index(start)
    z = text.index("KEY-----", a + len(start) + 64) + len("KEY-----")
    secret = text[a:z]
    return {"rule": "private-key", "secret": secret, "match": secret, "line": 1, "end_line": 1 + secret.count("\n"),
            "decoded": False}


def test_a_placeholder_header_is_masked_inside_its_own_string_only():
    line = json.dumps({"env": "API_KEY=-----BEGIN PRIVATE KEY-----...", "next": "x" * 90 + " -----END PUBLIC KEY----- tail"})
    out, counts = scan.redact(line, [_private_key_finding(line, "-----BEGIN")], pii=False, json_like=True)
    got = json.loads(out)
    assert got["env"] == "API_KEY=[REDACTED]" and got["next"].endswith("-----END PUBLIC KEY----- tail"), got
    assert counts["secrets"] == {"private-key": 1}


def test_a_match_across_records_keeps_every_line():
    text = "\n".join([json.dumps({"a": "check for -----BEGIN RSA PRIVATE KEY-----"}), json.dumps({"b": "y" * 100}),
                      json.dumps({"c": "-----END RSA PRIVATE KEY----- done"})]) + "\n"
    out, _ = scan.redact(text, [_private_key_finding(text, "-----BEGIN")], pii=False, json_like=True)
    lines = out.split("\n")
    assert len(lines) == len(text.split("\n"))
    assert json.loads(lines[0]) == {"a": "check for [REDACTED]"} and json.loads(lines[2])["c"].endswith(" done")


def test_a_real_key_inside_one_string_is_masked_whole():
    key = "-----BEGIN RSA PRIVATE KEY-----\\n" + PEM_BODY + "\\n-----END RSA PRIVATE KEY-----"
    line = '{"tool_result":"cat id_rsa\\n' + key + '\\n","ok":true}'
    out, _ = scan.redact(line, [_private_key_finding(line, "-----BEGIN")], pii=False, json_like=True)
    got = json.loads(out)
    assert "MIIEv" not in out and got["tool_result"] == "cat id_rsa\n[REDACTED]\n" and got["ok"] is True, got


def test_a_mask_never_cuts_an_escape():
    for text, start, stop in ((r'{"a":"x\ngho_A1b2C3d4E5f6G7h8\"y"}', "ngho_", '\\"y'),  # after \, before \"
                              (r'{"a":"x\u00e9tail ok"}', "00e9", " ok")):  # inside \u00e9
        a, z = text.index(start), text.index(stop) + 1
        jt = scan.JsonText(text)
        (a2, z2), = scan.json_spans(jt, a, z)
        assert text[a2] == "\\", (text, a2)
        json.loads(text[:a2] + scan.MASK + text[z2:])
        f = {"rule": "generic-api-key", "secret": text[a:z], "match": text[a:z], "line": 1, "decoded": False}
        json.loads(scan.redact(text, [f], pii=False, json_like=True)[0])


def test_a_multi_line_key_in_markdown_is_still_masked_whole():
    key = "-----BEGIN RSA PRIVATE KEY-----\n" + PEM_BODY.replace("\\n", "\n") + "\n-----END RSA PRIVATE KEY-----"
    text = "# notes\n\n" + key + "\n\nmore notes\n"
    out, _ = scan.redact(text, [_private_key_finding(text, "-----BEGIN")], pii=False, json_like=False)
    assert out == "# notes\n\n[REDACTED]\n\nmore notes\n", out


def test_json_text_broken_by_a_mask_raises():
    for before, after in (('{"a":"b"}\n', '{"a":"[REDACTED]\n'), ('{"a":1}\n{"b":2}\n', '{"a":[REDACTED]}\n'),
                          ('{"a":"x"}\n{"b":"y"}\n', '{"a":"[REDACTED]"}\n')):  # merged, and what is left parses
        try:
            scan._json_lines_intact(before, after)
        except RuntimeError:
            continue
        raise AssertionError(f"accepted {after!r}")


def test_staged_names_keep_the_kind():
    from collect.build import kind_of
    assert scan.json_like("ab12" + kind_of("x/full.jsonl") + ".txt") and scan.json_like("ab12" + kind_of("full.jsonl.003") + ".txt")
    assert scan.json_like("ab12" + kind_of("metadata.json") + ".txt") and scan.json_like("ab12.view.jsonl")
    assert not scan.json_like("ab12" + kind_of(".specstory/history/2026-01-01_x.md") + ".txt")


# -- tokens known by their format: found whatever surrounds them --------------------------------------------------
GOOGLE_KEY = "AIzaSyB1c2D3e4F5g6H7i8J9k0L1m2N3o4P5q6R"


def test_a_formatted_token_is_found_behind_any_escape():
    for text in (r'{\\x22apiKey\\x22:\\x22' + GOOGLE_KEY + r'\\x22}', r'"' + GOOGLE_KEY + r'"',
                 "q=%22" + GOOGLE_KEY + "%22", r'"a\n' + GOOGLE_KEY + '"', '{"k":"' + GOOGLE_KEY + '"}',
                 "token ghp_" + "Ab1" * 12 + " ok"):
        got = scan.strict_findings(text)
        assert len(got) == 1 and got[0]["rule"].startswith("strict:"), (text, got)
        out, counts = scan.redact(text, got, pii=False)
        assert GOOGLE_KEY not in out and "ghp_Ab1" not in out and sum(counts["secrets"].values()) == 1


def test_a_formatted_token_is_not_found_inside_a_longer_word():
    for text in ("x" + GOOGLE_KEY, GOOGLE_KEY + "Z", "ghp_" + "a" * 35, "my_ghp_" + "a" * 36):
        assert scan.strict_findings(text) == [], text


def test_emails_with_markdown_escapes_or_a_dashed_rule_after_them():
    for text, masked in ((r"mail first\_last@amd.com now", True), (r'"first\\_middle\\_last@amd.com"', True),
                         ("hi@greptile.com------", True), ("a@b.com-x", False), ("\\n@app.get(", False),
                         ("\\ngit@github.com:x", False), ("noreply@x.dev", False)):
        out, counts = redact(text, [])
        assert bool(counts["emails"]) == masked, (text, out)
        assert "amd.com" not in out and "greptile" not in out or not masked


# -- private keys by their own structure (review of 2026-10-03) ------------------------------------------------------
KEY_LINES = ["MIIEowIBAAKCAQEAx7Kp2Lm9Nz4Rt6Ws8Yb3Hd5Jf1GcQx7Kp2Lm9Nz4Rt6Ws8Yb3",
             "Hd5Jf1GcQx7Kp2Lm9Nz4Rt6Ws8Yb3Hd5Jf1GcQx7Kp2Lm9Nz4Rt6Ws8Yb3Hd5Jf1Gc",
             "Qx7Kp2Lm9Nz4Rt6Ws8Yb3Hd5Jf1GcQx7Kp2Lm9Nz4Rt6Ws8Yb3Hd5Jf1GcQx7Kp2Lm9=="]


def _patch_record() -> dict:
    """An edit's result as Claude Code stores it: the file a line per string."""
    lines = ["+-----BEGIN RSA PRIVATE KEY-----"] + ["+" + k for k in KEY_LINES] + ["+-----END RSA PRIVATE KEY-----",
                                                                               " unrelated = 1"]
    return {"type": "user", "toolUseResult": {"filePath": "/x/key.pem",
                                              "structuredPatch": [{"oldStart": 1, "lines": lines}]}}


def test_a_key_split_over_strings_is_masked_whole_in_json_lines_and_pretty_json():
    for text in (json.dumps(_patch_record()), json.dumps(_patch_record(), indent=2)):
        for findings in ([], [{"rule": "private-key", "line": 1, "decoded": False, "match": "",
                               "secret": text[text.index("-----BEGIN"):text.index("KEY-----", text.index("-----END")) + 8]}]):
            out, counts = scan.redact(text, findings, pii=False, json_like=True)
            assert not any(k[:20] in out for k in KEY_LINES), out
            lines = json.loads(out)["toolUseResult"]["structuredPatch"][0]["lines"]
            assert lines[-1] == " unrelated = 1" and counts["secrets"] == {"private-key": 1}, (lines, counts)


def test_placeholder_headers_lose_only_themselves_and_all_go_in_one_pass():
    records = [json.dumps({"env": f"KEY{i}=-----BEGIN PRIVATE KEY-----...", "note": "# --- section ---"})
               for i in range(300)]
    out, counts = scan.redact("\n".join(records) + "\n", [], pii=False, json_like=True)
    assert "BEGIN" not in out and counts["secrets"] == {"private-key": 300}
    assert all(json.loads(line)["note"] == "# --- section ---" for line in out.splitlines())


def test_a_key_in_markdown_with_no_end_is_masked_line_by_line():
    text = "# notes\n\n-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(KEY_LINES) + "\n\nmore text\n"
    out, counts = scan.redact(text, [], pii=False)
    assert out == "# notes\n\n[REDACTED]\n[REDACTED]\n[REDACTED]\n[REDACTED]\n\nmore text\n" and counts["secrets"] == {"private-key": 1}


def test_windows_home_paths_and_percent_encoded_emails():
    for text, name in (("PS C:\\Users\\jdoe> dir", "jdoe"), ("C:\\Users\\jdoe>dir", "jdoe"), ("x C:\\Users\\jdoe\n", "jdoe"),
                       ("c:\\users\\jdoe\\code", "jdoe"), (json.dumps("PS C:\\Users\\jdoe> ls"), "jdoe"),
                       ("cd C:\\Users\\jdoe && dir", "jdoe"), ("jane%40corp.io", "jane")):
        assert name not in redact(text, [])[0], text
    for text, kept in (("C:\\Users\\Public\\x", "Public"), ("npm i %40types%2Fnode", "types")):
        assert kept in redact(text, [])[0], text


def test_lines_at_a_time_mask_what_the_whole_text_would(tmp_path=None):
    import tempfile
    from pathlib import Path
    root = Path(tmp_path or tempfile.mkdtemp())
    token = "ghp_" + "Zx9" * 12
    records = [json.dumps({"i": i, "t": f"use {token} now" if i % 50 == 7 else "plain"}) for i in range(400)]
    records[123] = json.dumps(_patch_record())
    src = root / "big.jsonl"
    src.write_text("\n".join(records) + "\n")
    findings = [{"rule": "github-pat", "secret": token, "match": token, "line": 8, "decoded": False}]
    counts = scan.redact_lines(src, root / "out.jsonl", findings)
    out = (root / "out.jsonl").read_text()
    whole, _ = scan.redact(src.read_text(), findings, pii=False, json_like=True)
    assert out == whole and token not in out and counts["secrets"]["private-key"] == 1, counts
    try:
        scan.redact_lines(src, root / "never.jsonl", [{"rule": "x", "secret": "not-in-this-file-at-all",
                                                       "match": "", "line": 99999, "decoded": False}])
    except RuntimeError:
        assert not (root / "never.jsonl").exists()
    else:
        raise AssertionError("a finding placed nowhere was accepted")
    path, got = scan.strict_file(str(src))
    assert [f["line"] for f in got] == [i + 1 for i in range(400) if i % 50 == 7 and i != 123]


def test_a_match_over_several_lines_is_placed_on_its_secret_s_own_line():
    import tempfile
    from pathlib import Path
    root = Path(tempfile.mkdtemp())
    records = [json.dumps({"n": i}) for i in range(10)]
    records[4] = json.dumps({"cmd": "curl https://api.example.org/v1 \\"})
    records[5] = json.dumps({"cmd": "  -u admin:s3cretpw9 -X GET"})
    src = root / "s.jsonl"
    src.write_text("\n".join(records) + "\n")
    match = records[4][records[4].index("curl"):] + "\n" + records[5][:records[5].index("pw9") + 3]
    for secret in ("admin:s3cretpw9", "pw9"):  # a long secret, and a short one found through its line's part
        f = {"rule": "curl-auth-user", "secret": secret, "match": match, "line": 5, "end_line": 6, "decoded": False}
        scan.redact_lines(src, root / "out.jsonl", [f])
        out = (root / "out.jsonl").read_text().splitlines()
        assert secret not in out[5] and "[REDACTED]" in out[5] and out[4] == records[4], out[4:6]
        whole, _ = scan.redact(src.read_text(), [f], pii=False, json_like=True)
        assert secret not in whole


def test_a_private_key_match_across_records_is_masked_the_same_a_line_at_a_time():
    import tempfile
    from pathlib import Path
    root = Path(tempfile.mkdtemp())
    records = [json.dumps({"n": i}) for i in range(8)]
    records[2] = json.dumps({"check": "starts with -----BEGIN RSA PRIVATE KEY-----"})
    records[3] = json.dumps({"x": "y" * 90})
    records[4] = json.dumps({"z": "-----END RSA PRIVATE KEY----- and more"})
    text = "\n".join(records) + "\n"
    src = root / "k.jsonl"
    src.write_text(text)
    a = text.index("-----BEGIN")
    z = text.index("KEY-----", text.index("-----END")) + 8
    f = {"rule": "private-key", "secret": text[a:z], "match": text[a:z], "line": 3, "end_line": 5, "decoded": False}
    scan.redact_lines(src, root / "out.jsonl", [f])
    whole, _ = scan.redact(text, [f], pii=False, json_like=True)
    assert (root / "out.jsonl").read_text() == whole and "BEGIN" not in whole
    assert [json.loads(line) for line in whole.splitlines()][3:] == [json.loads(r) for r in records[3:]]


# -- keys of every shape, a line per string (second review, 2026-10-03) ------------------------------------------
_B = ["MIIEowIBAAKCAQEAx7Kp2Lm9Nz4Rt6Ws8Yb3Hd5Jf1GcQx7Kp2Lm9Nz4Rt6Ws8Yb3",
      "Hd5Jf1GcQx7Kp2Lm9Nz4Rt6Ws8Yb3Hd5Jf1GcQx7Kp2Lm9Nz4Rt6Ws8Yb3Hd5Jf1Gc",
      "Zq3Wn8Tv2Ry6Uo1Ip4As7Df0Gh5Jk9LzXcVbNm3Qw8Er2Ty6Ui1Op4As7Df0Gh5Jk"]
KEY_SHAPES = {
    "PGP": ["-----BEGIN PGP PRIVATE KEY BLOCK-----", "Version: GnuPG v2", "", *_B, "=abcd",
            "-----END PGP PRIVATE KEY BLOCK-----"],
    "encrypted RSA": ["-----BEGIN RSA PRIVATE KEY-----", "Proc-Type: 4,ENCRYPTED",
                      "DEK-Info: AES-128-CBC,0123456789ABCDEF0123456789ABCDEF", "", *_B, "-----END RSA PRIVATE KEY-----"],
    "Windows line endings": [x + "\r" for x in ["-----BEGIN RSA PRIVATE KEY-----", *_B, "-----END RSA PRIVATE KEY-----"]],
    "indented": ["    " + x for x in ["-----BEGIN OPENSSH PRIVATE KEY-----", *_B, "-----END OPENSSH PRIVATE KEY-----"]],
    "cut short, no END": ["-----BEGIN PGP PRIVATE KEY BLOCK-----", "Version: GnuPG v2", "", *_B],
    "a stray line inside": ["-----BEGIN RSA PRIVATE KEY-----", _B[0], "# (pasted from the vault)", *_B[1:],
                            "-----END RSA PRIVATE KEY-----"],  # only the look-ahead to its END masks this one
}


def test_every_shape_of_key_stored_a_line_per_string_is_masked_whole():
    for name, lines in KEY_SHAPES.items():
        rec = {"toolUseResult": {"structuredPatch": [{"lines": ["+" + x for x in lines] + [" after = 1"]}]}}
        for text in (json.dumps(rec), json.dumps(rec, indent=2)):
            out, counts = scan.redact(text, [], pii=False, json_like=True)
            assert not any(b[:20] in out for b in _B), (name, out)
            assert json.loads(out)["toolUseResult"]["structuredPatch"][0]["lines"][-1] == " after = 1", name
            assert counts["secrets"] == {"private-key": 1}, (name, counts)
        md = "notes\n\n" + "\n".join(lines) + "\n\nmore text\n"
        out, _ = scan.redact(md, [], pii=False)
        assert not any(b[:20] in out for b in _B) and out.endswith("\n\nmore text\n"), (name, out)


def test_a_key_left_unrecognised_stops_the_build_rather_than_be_published():
    lines = ["+" + x for x in KEY_SHAPES["encrypted RSA"]]
    text = json.dumps({"lines": lines})
    a = text.index("-----BEGIN")
    z = text.index("KEY-----", text.index("-----END")) + 8
    finding = {"rule": "private-key", "secret": text[a:z], "match": text[a:z], "line": 1, "decoded": False}
    real = scan.pem_spans
    scan.pem_spans = lambda text, jt=None: []  # as if the header were not recognised
    try:
        scan.redact(text, [finding], pii=False, json_like=True)
    except RuntimeError as e:
        assert "survives masking" in str(e)
    else:
        raise AssertionError("a key's body was let through")
    finally:
        scan.pem_spans = real


def test_a_placeholder_before_a_real_key_costs_only_itself():
    lines = ["KEY=-----BEGIN RSA PRIVATE KEY-----", "HOST=example.org", "PORT=5432",
             "-----BEGIN RSA PRIVATE KEY-----", *_B, "-----END RSA PRIVATE KEY-----", "after"]
    out, counts = scan.redact(json.dumps({"lines": lines}), [], pii=False, json_like=True)
    got = json.loads(out)["lines"]
    assert got[:3] == ["KEY=[REDACTED]", "HOST=example.org", "PORT=5432"] and got[-1] == "after", got
    assert not any(b[:20] in out for b in _B) and counts["secrets"] == {"private-key": 2}, (got, counts)


def test_a_line_break_after_a_windows_users_folder_is_not_a_separator() -> None:
    """In JSON a lone backslash starts an escape: `C:\\\\Users\\nC:` once took the `\\n` for a separator and masked `nC`
    as a name, which broke the escape (two Entire transcripts stopped the first full build, 2026-10-03)."""
    line = json.dumps({"out": "dir:\nC:\\Users\nC:\\Users\\jane\\x\nC:\\Users\\Alice Smith\\y && dir", "p": "C:/Users/bob/z"})
    out, counts = scan.redact(line, [], pii=True, json_like=True)
    value = json.loads(out)
    assert "jane" not in out and "Alice Smith" not in out and "bob" not in out and counts["home_users"] == 3
    assert value["out"].startswith("dir:\nC:\\Users\nC:\\Users\\[REDACTED]\\x")
    nested = json.dumps({"args": json.dumps({"cwd": "C:\\Users\\jane\\proj"})})   # JSON held in a JSON string
    out, _ = scan.redact(nested, [], pii=True, json_like=True)
    assert json.loads(json.loads(out)["args"])["cwd"] == "C:\\Users\\[REDACTED]\\proj"
    out, _ = scan.redact("C:\\Users\\jane\\x", [], pii=True, json_like=False)   # plain text: one backslash separates
    assert "jane" not in out


def test_gemini_unescaped_windows_paths_and_escaped_letters_in_names() -> None:
    gemini = '{"text": "cd C:\\Users\\alice\\proj"}'   # Gemini CLI writes the backslashes unescaped
    out, counts = scan.redact(gemini, [], pii=True, json_like=True)
    assert "alice" not in out and counts["home_users"] == 1
    line = json.dumps({"p": "/Users/J\u00f6rg/x", "e": "jos\u00e9@corp-mail.io"})   # \u00f6, \u00e9 in the text
    out, counts = scan.redact(line, [], pii=True, json_like=True)
    assert json.loads(out) == {"p": f"/Users/{MASK}/x", "e": MASK} and counts == {"secrets": {}, "emails": 1, "home_users": 1}


def test_escaped_letters_in_names_at_any_depth() -> None:
    once = json.dumps({"p": "C:\\Users\\J\u00f6rg\\x", "q": "C:\\Users\\Jos\u00e9 Luis\\y"})   # \u00f6 escaped once
    out, counts = scan.redact(once, [], pii=True, json_like=True)
    assert "00f6rg" not in out and "00e9 Luis" not in out and counts["home_users"] == 2, out
    twice = json.dumps({"r": json.dumps({"p": "/Users/J\u00f6rg/x", "e": "zo\u00eb@corp.io"})})   # escaped twice
    out, counts = scan.redact(twice, [], pii=True, json_like=True)
    assert "00f6rg" not in out and "corp.io" not in out and counts == {"secrets": {}, "emails": 1, "home_users": 1}, out


KEY_BODY = ("MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun\n"
            "VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK\n")


def test_a_keys_second_half_with_its_end_and_no_header_is_masked() -> None:
    tail = KEY_BODY + "-----END RSA PRIVATE KEY-----\n"
    out, counts = scan.redact("tail -3 key.pem:\n" + tail, [], pii=False)
    assert "MIIEowIBAAKC" not in out and "VTLw7onL" not in out and counts["secrets"] == {"private-key": 1}, out
    line = json.dumps({"content": "tail -3 key.pem:\n" + tail})   # in a JSON string, the line breaks escaped
    out, _ = scan.redact(line, [], pii=False, json_like=True)
    assert "VTLw7onL" not in out and json.loads(out)["content"].startswith("tail -3 key.pem:\n")
    prose = "the file ends with -----END RSA PRIVATE KEY----- as usual"   # no body before it: nothing to mask
    assert scan.redact(prose, [], pii=False)[0] == prose


TAIL_LINES = ["MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun",
              "VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK"]


def _no_key(out: str) -> bool:
    return not any(x[:12] in out or x[-12:] in out for x in TAIL_LINES)


def test_a_keys_tail_is_masked_however_its_last_lines_run() -> None:
    tails = {"a short last line": "\n".join(TAIL_LINES + ["AB=="]) + "\n-----END RSA PRIVATE KEY-----\n",
             "a PGP checksum": "\n".join(TAIL_LINES + ["xQ==", "=XyZw"]) + "\n-----END PGP PRIVATE KEY BLOCK-----\n",
             "Windows line ends": "\r\n".join(TAIL_LINES) + "\r\n-----END RSA PRIVATE KEY-----\r\n",
             "indented": "".join("    " + x + "\n" for x in TAIL_LINES) + "    -----END RSA PRIVATE KEY-----\n",
             "a diff": "".join("+" + x + "\n" for x in TAIL_LINES) + "+-----END RSA PRIVATE KEY-----\n",
             "one line": "".join(TAIL_LINES) + "-----END RSA PRIVATE KEY-----"}
    for name, tail in tails.items():
        out, counts = scan.redact("$ tail -3 key.pem\n" + tail, [], pii=False)
        assert _no_key(out) and out.startswith("$ tail -3 key.pem") and counts["secrets"] == {"private-key": 1}, (name, out)
        for depth in (1, 2):   # in JSON, and in JSON held in a JSON string
            line = json.dumps({"content": "$ tail -3 key.pem\n" + tail})
            line = json.dumps({"text": line}) if depth == 2 else line
            out, counts = scan.redact(line, [], pii=False, json_like=True)
            got = json.loads(out)
            got = json.loads(got["text"]) if depth == 2 else got
            assert _no_key(out) and got["content"].startswith("$ tail -3 key.pem\n"), (name, depth, out)
            assert counts["secrets"] == {"private-key": 1}, (name, depth, counts)


def test_a_tail_longer_than_the_first_window_is_masked_whole() -> None:
    import base64

    lines = [base64.b64encode(bytes([i]) * 48).decode() for i in range(200)]   # 13 KB, past the first 4 KB read
    out, counts = scan.redact("$ tail key.pem\n" + "\n".join(lines) + "\n-----END RSA PRIVATE KEY-----\n", [], pii=False)
    assert out == "$ tail key.pem\n" + scan.MASK + "\n" and counts["secrets"] == {"private-key": 1}, out[:200]


def test_a_tail_after_text_or_an_escape_on_its_first_line_keeps_the_json() -> None:
    # An escape just before the run is masked with it, whole (`\\t`, `\\u00e9`, an escaped backslash).
    for before, kept in (("x\t", "x"), ("Jos\u00e9", "Jos"), ('say "', 'say "'), ("C:\\temp\\", "C:\\temp"),
                         ("see: ", "see: ")):
        line = json.dumps({"content": before + "\n".join(TAIL_LINES) + "\n-----END RSA PRIVATE KEY-----"})
        out, counts = scan.redact(line, [], pii=False, json_like=True)
        assert _no_key(out) and json.loads(out)["content"] == kept + scan.MASK, (before, out)


def test_a_tail_in_json_held_in_json_leaves_no_half_escape() -> None:
    """The partner's review of ddef7de: an escape before a tail's first line, one level down, was split by the mask
    (`\\\\t` + run became `\\\\[REDACTED]`), and the inner JSON no longer parsed."""
    for before in ("x\t", "Jos\u00e9", "26\u2192", "key \U0001f511", "C:\\temp\\", 'say "'):
        inner = json.dumps({"content": before + "\n".join(TAIL_LINES) + "\n-----END RSA PRIVATE KEY-----"})
        for ensure_ascii in (True, False):
            out, _ = scan.redact(json.dumps({"arguments": inner}, ensure_ascii=ensure_ascii), [], pii=False, json_like=True)
            got = json.loads(json.loads(out)["arguments"])["content"]
            assert _no_key(out) and got.endswith(scan.MASK), (before, out)
            assert not any(0xD800 <= ord(c) <= 0xDFFF for c in got), (before, got)


def test_a_second_keys_tail_after_a_whole_key_is_masked_and_each_counted_once() -> None:
    whole = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(TAIL_LINES) + "\n-----END RSA PRIVATE KEY-----\n"
    tail = "\n".join(x[::-1] for x in TAIL_LINES) + "\n-----END RSA PRIVATE KEY-----\n"
    out, counts = scan.redact(whole + "and the other one:\n" + tail, [], pii=False)
    assert _no_key(out) and TAIL_LINES[0][::-1][:12] not in out and counts["secrets"] == {"private-key": 2}, out


def test_a_tail_in_tool_output_with_line_marks_is_masked() -> None:
    """The partner's review of ddef7de: a tail read at an offset (the Read tool's `26→`, `cat -n`), grepped for its
    END (`grep -n -B`), tab-indented or quoted was left whole."""
    end = "-----END RSA PRIVATE KEY-----"
    lines = TAIL_LINES * 4 + ["AB==xQ12abcd"]   # 8 lines of 64, then a 12-character last line
    shapes = {"Read": lambda n, x: f"{n:>6}\u2192{x}",
              "cat -n": lambda n, x: f"{n:>6}\t{x}",
              "grep -n -B": lambda n, x: f"key.pem:{n}:{x}" if x == end else f"key.pem-{n}-{x}",
              "grep -n -B, one file": lambda n, x: f"{n}:{x}" if x == end else f"{n}-{x}",
              "tab-indented": lambda n, x: f"\t{x}",
              "quoted": lambda n, x: f"> {x}",
              "Devin": lambda n, x: f"{n:>3}|{x}",
              "a box view": lambda n, x: f"{n:>7} \u2502 {x}"}
    for name, mark in shapes.items():
        text = "$ claude: Read key.pem, from line 20\n" + "\n".join(mark(20 + i, x) for i, x in enumerate(lines + [end])) + "\n"
        for form in ("text", "json", "json, the arrow as is", "json in json"):
            t = (text if form == "text" else json.dumps({"content": text}, ensure_ascii=form != "json, the arrow as is"))
            t = json.dumps({"text": t}) if form == "json in json" else t
            out, counts = scan.redact(t, [], pii=False, json_like=form != "text")
            assert not any(x[:12] in out or x[-12:] in out for x in lines), (name, form, out[:300])
            assert counts["secrets"] == {"private-key": 1} and "Read key.pem" in out, (name, form, counts)
            if form != "text":
                json.loads(out)


def test_an_end_after_marked_prose_is_left() -> None:
    for text in ("    20\u2192Here are the steps:\n    21\u2192Done\n    22\u2192-----END RSA PRIVATE KEY-----\n",
                 "notes.md-3-see below\nnotes.md:4:-----END RSA PRIVATE KEY-----\n",
                 "> quoted text here\n> -----END RSA PRIVATE KEY-----\n",
                 '    15\u2192    if pem.endswith("-----END RSA PRIVATE KEY-----"):\n',
                 "x: -----END RSA PRIVATE KEY-----",
                 "| step | result |\n|---|---|\n| -----END RSA PRIVATE KEY----- | ok |\n",
                 "  9|    return True\n 10|-----END RSA PRIVATE KEY-----\n"):
        assert scan.pem_spans(text) == [], text
        line = json.dumps({"content": text})
        assert scan.redact(line, [], pii=False, json_like=True)[0] == line, text


def test_an_end_that_is_only_mentioned_is_left() -> None:
    for text in ("the file ends with -----END RSA PRIVATE KEY----- as usual",
                 "The private key file always ends with -----END RSA PRIVATE KEY-----",
                 'if pem.strip().endswith("-----END RSA PRIVATE KEY-----"):',
                 "```\n-----END RSA PRIVATE KEY-----\n```",
                 "Done\nOK\n-----END RSA PRIVATE KEY-----\n",
                 "-----END RSA PRIVATE KEY-----"):
        assert scan.pem_spans(text) == [], text
        line = json.dumps({"content": text})
        assert scan.redact(line, [], pii=False, json_like=True)[0] == line, text


def test_the_walk_back_from_an_end_is_linear() -> None:
    """The review of rc3: a pattern of nested runs took exponential time on a long run, and each of these hung a
    worker. In a process of its own, so a hang fails the test rather than stopping the suite."""
    import os
    import subprocess
    import sys

    code = r'''
import base64, json, time
from collect import scan
b64 = base64.b64encode(bytes(range(256)) * 300).decode()
line = "MIIEowIBAAKCAQEAx" + "Q" * 47
mention = json.dumps({"content": 'if pem.strip().endswith("-----END RSA PRIVATE KEY-----"):'})
texts = [json.dumps({"content": "ssh-rsa " + b64[:372] + " dev@laptop"}) + "\n" + mention,
         "\n".join([line] * 25) + '"-----END RSA PRIVATE KEY-----',
         json.dumps({"content": b64[:6000]}) + "\n" + mention,
         "\n".join(b64[i:i + 64] for i in range(0, 64000, 64)) + '\n"-----END RSA PRIVATE KEY-----',
         "\n".join(b64[i:i + 64] for i in range(0, 64000, 64)) + "\n-----END RSA PRIVATE KEY-----",
         " ".join(["AAAAbbbbCCCCdddd"] * 3800) + "\n-----END PRIVATE KEY-----",
         "\\" * 60000 + "n-----END PRIVATE KEY-----",
         "AAAAbbbbCCCCdddd\n" * 4000 + "-----END PRIVATE KEY-----",
         "ab\n-----END RSA PRIVATE KEY-----\n" * 5000,
         "x" * 70000 + "AAAAbbbbCCCCdddd-----END PRIVATE KEY-----",
         ("A" * 20 + "-----END RSA PRIVATE KEY-----") * 20000]
t = time.perf_counter()
for x in texts:
    scan.pem_spans(x)
    scan.redact(x, [], pii=True)
    scan.redact(json.dumps({"c": x}), [], pii=True, json_like=True)
print(round(time.perf_counter() - t, 3))
'''
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=root,
                         env={**os.environ, "PYTHONPATH": str(root)})
    assert got.returncode == 0 and float(got.stdout) < 10, (got.stdout, got.stderr[-800:])


def _found(secret, line, decoded=True, rule="generic-api-key"):
    return {"rule": rule, "secret": secret, "match": f"key={secret}", "line": line, "end_line": line, "decoded": decoded}


def test_a_secret_percent_encoded_twice_is_placed_where_it_is_written():
    """The full build of 2026-10-04 stopped here: gitleaks decoded `w%253D` through both layers and reported `w=`."""
    secret = "kAOq7Zr2Lm9Nx4Pt8Wb3Hd5Jf1Gw="
    text = f"see https://x.io/cb?token={secret[:-1]}%253D&next=1\nbye\n"
    out, counts = scan.redact(text, [_found(secret, 1)], pii=False)
    assert secret[:-1] not in out and out.endswith("&next=1\nbye\n") and counts["secrets"] == {"generic-api-key": 1}, out


def test_a_finding_no_rule_places_masks_the_lines_the_scan_named():
    secret = "QmFzZTY0SW5zaWRlQmFzZTY0IQ=="   # as if decoded through a layer placing does not undo
    text = "first line\nsecond: opaque blob that holds it\nthird line\n"
    out, counts = scan.redact(text, [_found(secret, 2)], pii=False)
    assert out == f"first line\n{scan.MASK}\nthird line\n" and counts["secrets"] == {"generic-api-key (whole line)": 1}
    twice = "a\nheld here\nb\nheld here too\n"   # one secret named on two lines: both
    out, _ = scan.redact(twice, [_found(secret, 2), _found(secret, 4)], pii=False)
    assert out == f"a\n{scan.MASK}\nb\n{scan.MASK}\n", out
    line = json.dumps({"type": "user", "message": {"content": "an opaque blob that holds it somewhere inside"},
                       "note": "another value on the line, long enough to hold it", "n": 3})
    out, counts = scan.redact(line + "\n", [_found(secret, 1)], pii=False, json_like=True)
    got = json.loads(out)
    assert (got["type"], got["message"]["content"], got["note"], got["n"]) == ("user", scan.MASK, scan.MASK, 3)   # short ones stay
    assert counts["secrets"] == {"generic-api-key (whole line)": 1}


def test_the_last_resort_in_a_json_record_masks_only_the_strings_the_scan_pins_the_secret_to():
    """A DataClaw record is a whole session on one line: a secret found only through a layer placing does not undo
    cost every long string of the record (1,366 of 3,942 messages, version 2026-10-04). The scan, run again on the
    line, pins it to the strings that hold it; the others stay. Where the scan cannot tell, the last resort stays."""
    secret = "QmFzZTY0SW5zaWRlQmFzZTY0IQ=="
    msgs = [{"role": "user", "content": f"message number {i} with enough words to be long"} for i in range(9)]
    msgs[5]["content"] = "an encoded blob that, decoded, holds the key"
    line = json.dumps({"messages": msgs}) + "\n"

    def refind(text, f):   # as the scan finds it: while the blob is there
        return msgs[5]["content"] in text
    out, counts = scan.redact(line, [_found(secret, 1)], pii=False, json_like=True, refind=refind)
    assert [m["content"] for m in json.loads(out)["messages"]] == [scan.MASK if i == 5 else m["content"] for i, m in enumerate(msgs)]
    assert counts["secrets"] == {"generic-api-key (the strings that hold it)": 1}
    for unsure in (lambda t, f: False, lambda t, f: True, None):   # never found as written; found whatever is masked; no scan
        out, counts = scan.redact(line, [_found(secret, 1)], pii=False, json_like=True, refind=unsure)
        assert all(m["content"] == scan.MASK for m in json.loads(out)["messages"])
        assert counts["secrets"] == {"generic-api-key (whole line)": 1}


def test_a_header_with_no_end_leaves_the_records_next_field():
    """After a header whose key has no end in its string, the record's next string was masked as the key's first
    line: the next message's role, so a developer's message had no author (DataClaw, version 2026-10-04). A word is
    no line of a key; a key's lines after a header are still masked."""
    msgs = [{"role": "assistant", "content": "Paste it like this: -----BEGIN RSA PRIVATE KEY----- then the body"},
            {"role": "user", "content": "Did you fix the root of those issues?"}]
    out, _ = scan.redact(json.dumps({"messages": msgs}) + "\n", [], pii=False, json_like=True)
    got = json.loads(out)["messages"]
    assert got[1] == msgs[1] and got[0]["role"] == "assistant" and "PRIVATE KEY" not in got[0]["content"], got
    body = "MIIEowIBAAKCAQEAzQ4mF7Xb2hJk9LpQ3rT8vWnY1cEaGd6uHs0iKo5Ml2Nj4Pq7Rt"
    lines = {"patch": ["-----BEGIN RSA PRIVATE KEY-----", body, "wxyz", "ABCD=="]}
    got = json.loads(scan.redact(json.dumps(lines) + "\n", [], pii=False, json_like=True)[0])["patch"]
    assert got[1:] == [scan.MASK] * 3, got   # once a key's lines have begun, a short one in lowercase is one of them


def test_line_at_a_time_masking_has_the_same_last_resort(tmp_path):
    secret = "QmFzZTY0SW5zaWRlQmFzZTY0IQ=="
    src = tmp_path / "big.jsonl"
    lines = [json.dumps({"i": i, "text": f"record {i} with an opaque blob inside"}) for i in range(5)]
    src.write_text("\n".join(lines) + "\n")
    counts = scan.redact_lines(src, src, [_found(secret, 3)])
    got = [json.loads(x) for x in src.read_text().splitlines()]
    assert got[2] == {"i": 2, "text": scan.MASK}
    assert [g["text"] for g in got[:2] + got[3:]] == [f"record {i} with an opaque blob inside" for i in (0, 1, 3, 4)]
    assert counts["secrets"] == {"generic-api-key (whole line)": 1}


def test_a_decoded_secrets_escapes_are_read_in_linear_time() -> None:
    """The review of rc6: a run of backslashes could be shared among a secret's parts in many ways, each tried in
    turn, so a key's copy cut short took exponential time in its lines (over 30 s at its 24th line, doubling with
    each). In a process of its own, so a hang fails the test rather than stopping the suite."""
    import os
    import subprocess
    import sys

    code = r'''
import json, random, string, time
from collect import scan
random.seed(7)
body = ["".join(random.choice(string.ascii_letters + string.digits + "+/") for _ in range(64)) for _ in range(50)]
key = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(body) + "\n-----END RSA PRIVATE KEY-----"
t = time.perf_counter()
for depth in (1, 2):   # the secret as a decoded view reads it: one or two backslashes a line break
    secret = key
    for _ in range(depth):
        secret = json.dumps(secret)[1:-1]
    full = json.dumps(secret)[1:-1]   # one level down, as the staged file holds it
    for copy in (secret, full):
        cut = copy[: copy.index(body[40])] + body[40][:30] + "..."
        text = json.dumps({"out": cut + " more"}) + "\n" + json.dumps({"in": full})
        assert scan.place(text, {"rule": "private-key", "secret": secret, "decoded": True}), depth
# A run before a character whose escape ends in that character (`\\"`): the run read whole, so one way only.
secret = "".join(f'k{i:02d}\\"' for i in range(40))
full = json.dumps(secret)[1:-1]
text = json.dumps({"out": full[: len(full) * 3 // 4] + "..."}) + "\n" + json.dumps({"in": full})
assert scan.place(text, {"rule": "generic-api-key", "secret": secret, "decoded": True})
print(round(time.perf_counter() - t, 3))
'''
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=root,
                         env={**os.environ, "PYTHONPATH": str(root)})
    assert got.returncode == 0 and float(got.stdout) < 10, (got.stdout, got.stderr[-800:])


def test_a_secret_placed_by_no_rule_on_two_lines_of_a_large_file_is_masked_on_both(tmp_path) -> None:
    """The review of rc6: redact_lines took a line whole only when it was the one line the scan named for a secret, so
    a secret decoded through layers placing does not undo, named on two lines, stopped the build; redact() masks both."""
    import base64

    secret = "sk_live_A1b2C3d4E5f6G7h8J9k0"
    enc = base64.b64encode("".join(f"%{ord(c):02X}" for c in secret).encode()).decode()   # placed by no rule
    lines = [json.dumps({"type": "user", "text": enc}), json.dumps({"type": "tool", "out": "x " + enc})]
    src = tmp_path / "full.jsonl"
    src.write_text("\n".join(lines) + "\n")
    scan.redact_lines(src, tmp_path / "out.jsonl", [_found(secret, 1), _found(secret, 2)])
    out = (tmp_path / "out.jsonl").read_text()
    assert enc not in out and [json.loads(x)["type"] for x in out.splitlines()] == ["user", "tool"]


def test_a_backslash_before_a_character_written_as_an_escape_is_found() -> None:
    """The escape of the character after a run of backslashes may have its own backslashes read by the run (review of
    rc6): `\\` then a line break is `\\\\\\n` in JSON, `\\` then `é` is `\\\\\\u00e9` in ASCII-only JSON."""
    for secret in ("tok\\\ndef456", "tok\\édef456", "tok\\\\\tdef456"):
        for ascii_only in (True, False):
            text = json.dumps({"c": secret}, ensure_ascii=ascii_only)
            assert scan.place(text, {"rule": "generic-api-key", "secret": secret, "decoded": True}), (secret, text)


def test_a_match_over_lines_placed_by_no_rule_still_stops_a_large_files_masking(tmp_path) -> None:
    """redact_lines' last resort takes a line whole only for a match on that line alone; a match over several lines
    that no rule places raises, as before rc6."""
    import base64

    import pytest

    secret = "sk_live_A1b2C3d4E5f6G7h8J9k0"
    enc = base64.b64encode("".join(f"%{ord(c):02X}" for c in secret).encode()).decode()
    src = tmp_path / "full.jsonl"
    src.write_text(json.dumps({"text": enc}) + "\n" + json.dumps({"text": "next"}) + "\n")
    with pytest.raises(RuntimeError, match="cannot be placed"):
        scan.redact_lines(src, tmp_path / "out.jsonl", [{**_found(secret, 1), "end_line": 2}])


def test_json_strings_are_tokenized_in_one_pass_and_alike():
    """Issue #12: JsonText's strings found in one pass over escape pairs and quotes, the same tokens as a pattern for
    whole strings on a well-formed line, and in linear time on a line ending inside a string."""
    import os
    import re
    import subprocess
    import sys
    from pathlib import Path

    whole = re.compile(r'"(?:[^"\\\n]|\\.)*"')
    for line in ('{"a": "x", "b": ["y\\"z", {"c": "w\\\\"}], "d": 1}', '  "key" : "value with \\u00e9 and \\\\n",',
                 '[]', '"just a string"'):
        jt = scan.JsonText(line)
        old = []
        for m in whole.finditer(line):
            j = m.end()
            while j < len(line) and line[j] in " \t\r":
                j += 1
            old.append((m.start(), m.end() - 1, j < len(line) and line[j] == ":"))
        assert jt.tokens(0, len(line)) == old, line
    code = r"""
import time
from collect import scan
line = '{"a": "start ' + ('x' * 90 + '\\"') * 16000
t = time.perf_counter()
jt = scan.JsonText(line)
assert jt.tokens(0, len(line))[0][:2] == (1, 3)
print(round(time.perf_counter() - t, 3))
"""
    root = Path(__file__).resolve().parents[1]
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=root,
                         env={**os.environ, "PYTHONPATH": str(root)})
    assert got.returncode == 0 and float(got.stdout) < 5, (got.stdout, got.stderr[-800:])


def test_a_run_after_a_keys_prefix_is_masked_whatever_its_length(tmp_path):
    """Version 2026-10-04 published Anthropic keys cut short or broken over lines (75 to 91 characters where the scan's
    rule wants 108) and GitHub tokens, unmasked (#17's evaluation, 2026-10-05)."""
    body = "Ab3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY3zA5bC7dE9fG2hJ4kL6mN8pQ0rS2tU4vW6xY8zA0bC2dE4fG6hJ8kL"   # 86 characters
    cut = f"api_key: 'sk-ant-api03-{body}\n" + "Qx7Zr2Lp9Wm4AA'"
    out, counts = redact(cut, [], pii=False)
    assert out == f"api_key: '{MASK}\nQx7Zr2Lp9Wm4AA'" and counts["secrets"] == {"key-run": 1}
    whole = f"sk-ant-api03-{body}Qx7Zr2Lp9AA"
    out, counts = redact(f"KEY={whole} again {whole}", [f("anthropic-api-key", whole)], pii=False)
    assert out == f"KEY={MASK} again {MASK}" and counts["secrets"] == {"anthropic-api-key": 2}   # the scan's, placed first
    for text in ("token ghp_" + "a1B2c3D4e5" * 4, "github_pat_" + "11ABCDEFG0" * 6, "OPENAI=sk-proj-" + "x" * 24,
                 "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxx"):   # a placeholder after a prefix goes too
        assert MASK in redact(text, [], pii=False)[0], text[:20]
    for text in ("sk-ant-api03-short", "the risk-ant-api03-ab mention", "ghp_tooShort1", "sk-ant-" + "q" * 40):
        assert redact(text, [], pii=False)[0] == text, text[:20]
    src = tmp_path / "big.jsonl"   # a large file's lines with no finding are written as read, unless a run is there
    src.write_text(json.dumps({"m": f"sk-ant-api03-{body}"}) + "\n" + json.dumps({"m": "plain"}) + "\n")
    counts = scan.redact_lines(src, src, [])
    assert src.read_text() == json.dumps({"m": MASK}) + "\n" + json.dumps({"m": "plain"}) + "\n"
    assert counts["secrets"] == {"key-run": 1}


def test_a_text_listed_as_not_a_secret_is_no_finding(tmp_path, monkeypatch):
    # gitleaks' generic rule takes a dtype or a class name for a key; listed by digest, the scan drops it, keeping the rest
    name, key = "torch.bfloat16", "Zx8kQ2mV9pL4wR7tY1nB6cJ3"
    listed = tmp_path / "not_secrets.sha256"
    listed.write_text(hashlib.sha256(name.encode()).hexdigest() + "  a dtype\n# a note\n")
    found = [{"rule": "generic-api-key", "secret": name, "line": 1}, {"rule": "generic-api-key", "secret": key, "line": 2}]
    monkeypatch.setattr(scan, "find", lambda paths, workdir, procs: {str(paths[0]): list(found)})
    monkeypatch.setattr(scan, "strict_file", lambda p: (p, []))
    p = tmp_path / "a.jsonl"
    p.write_text("{}\n")
    assert [f["secret"] for f in scan.find_all([p], tmp_path / "w", 1)[str(p)]] == [name, key]   # no list: both
    monkeypatch.setenv(scan.NOT_SECRETS_ENV, str(listed))
    assert [f["secret"] for f in scan.find_all([p], tmp_path / "w", 1)[str(p)]] == [key]
    monkeypatch.setenv(scan.NOT_SECRETS_ENV, str(tmp_path / "missing"))
    assert len(scan.find_all([p], tmp_path / "w", 1)[str(p)]) == 2                                  # a missing list: none
