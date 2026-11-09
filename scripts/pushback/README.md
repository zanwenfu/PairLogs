# Pushback labels

The `pushback` column of `conversations`: gpt-6-luna's reading of each message a developer typed, pushback or not,
from the message's text alone (no session context, no reasoning), one call per distinct text. Run on the VM against
a built version; `luna.py` reads `AZURE_OPENAI_BASE_URL` and `AZURE_OPENAI_API_KEY` from `~/errata-bench/.env`.

1. `extract_typed.py VERSION_DIR DIR/texts.jsonl DIR/map.parquet`: every typed row's text, one line per distinct
   text, and each row's (session_key, turn_number) with its text's id.
2. `label_all.py DIR/texts.jsonl DIR/labels.jsonl [CAP_USD]`: one call per distinct text, paced over two deployments
   of the model (Global Standard, 2,000 requests a minute, and US Data Zone Standard, 667). Each answer is held to
   `pushback` or `not_pushback` by a strict JSON schema. Results are appended as they arrive; a restart skips those
   done (a torn last line is cut first). A message Azure's content filter refuses is recorded as `content_filter`,
   another refusal by its code (`cyber_policy`, `refused (model)`), with no label.
3. `retry_failed.py DIR`: the texts without a label, but for an empty text, labelled again, up to three rounds;
   their new lines added to `labels.jsonl`, where a later line for an id replaces an earlier one. The content filter
   refuses the same text every time under the same setting; its refusals are asked again for a setting changed since
   (recorded by hand in `DIR/filter.json`, which the join copies into the run's record).
   `copies.py VERSION_DIR DIR/copies.parquet`: typed rows that copy an earlier row (`copy_of`), for the join's count of
   copies whose text and label differ from the message they copy.
4. `join_labels.py DIR`: `DIR/pushback.parquet` (session_key, turn_number, pushback) for every typed row, and
   `DIR/meta.json`, the run's record: model, dates, the prompt and answer format as sent, the texts cut, the counts,
   the copies whose text and label differ, cost at list price. It refuses if a text has no result, or has no label for
   a reason other than a refusal (an error left after the retries).
5. Build with `ERRATA_PUSHBACK_LABELS=DIR/pushback.parquet`: typed rows take their label, and the changelog's
   `pushback_labels` is `meta.json`.

Labels for the texts a later version adds, without a rebuild:

6. `bedrock_luna.py LUNA_PY IN.jsonl OUT.jsonl [--workers 16] [--max-usd 3]`: the same request as `luna.py` (its
   instructions, input, answer format, no reasoning, 32 output tokens, read from `luna.py`'s source without running
   it) sent to gpt-6-luna on AWS Bedrock (`global.openai.gpt-6-luna`, the OpenAI-compatible Responses endpoint,
   `AWS_BEARER_TOKEN_BEDROCK` from the environment). Resumable; no call starts once the recorded spend could pass the
   cap. Used on 2026-10-27/28 for the 20,482 texts new in version 2026-10-27; on 200 texts the Azure run had labelled,
   drawn at random, the two agreed on 188 (AWS said pushback where Azure did not on 11, the reverse on 1).
7. `apply_by_text.py VERSION_DIR STORE OLD_CHANGELOG BACKUP [--dry] [--no-corrected] [--meta EXTRA.json]`: a built
   version's typed rows take the store's labels by their text's id (`collect.pushback.apply`), the changelog's
   `pushback_labels` gets this version's counts (EXTRA.json's fields last, e.g. `meta_2026-10-28.json`, which names
   both services), and the card is written again.
