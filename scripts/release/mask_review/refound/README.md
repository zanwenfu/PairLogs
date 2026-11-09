# The values found again after the first mask (2026-11-05 evening)

Run on the cluster after th2b2 (the 319-value mask). Each prints shapes (letters a, digits 9), counts and digests,
never a value; the value lists stay in the cluster's private folder.

- gl_line.py, gl_full.py, gl_digest.py: the one gitleaks finding left. The whole file before and after the mask; the
  finding is a path piece that gitleaks cut where it splits a 19.7 MB line; its digest went on the not-secrets list.
- th_refound.py: the 40 masked values TruffleHog still found, by decoder, detector and file.
- refound_where.py: where they stand as written (every string, nested ones too, with escape-tolerant matching): 31 in
  OpenHands feedback's nested `trajectory`, which mask_finding.py did not read (fixed in f48e3f2).
- refound_forms.py, refound_written.py: the other 9 by JSON, HTML and URL unescaping, and through base64.
- extra_forms.py: the written forms to add (two JDBC strings over a line break, a URI unquoted, the three base64 runs
  holding two Gemini keys).
- th2c.sbatch: the release check again with the new not-secrets list (PASS). th2d.sbatch: the count with 325 values.
  th2e.sbatch: the second masking pass and every check after it.
- new18_contexts.py: the 18 values TruffleHog flagged after the second pass that the scan before any mask never found,
  each by its surroundings (words kept, digits and long runs by shape): tails of masked strings, ids, hashes, placeholders.

