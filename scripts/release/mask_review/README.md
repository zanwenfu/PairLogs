# How the TruffleHog values to mask were chosen (2026-11-05)

The count pass (mask_finding.py --values --per-value, count only) over tier A found that masking every one of the
6,775 values TruffleHog flagged (identifier-column values already left out) would change 617,872 places in 5,818
sessions, and that nearly all of them are no secret: Codex turn ids and other ids inside raw session files, git
hashes, pieces of base64 images, a line of dashes, the "Co-Authored-By: ... noreply" line, skill names, paths. None of
the 6,775 was verified live by TruffleHog. These scripts, run in order on the cluster, never print a value (only its
shape: letters a, digits 9):

1. common_values.py: the values by kind and frequency; the 60 most frequent in context.
2. value_contexts.py: each value's class from the 50 characters before it (a key name, an id name, other).
3. other_values.py: the 'other' values that cannot be a key set aside by shape (UUID, 40/64-hex hash, inside base64).
4. final_rule.py: the first rule; then zero_where.py, encoded_forms.py and conn_where.py found where values that
   occur nowhere as written stand (escaped forms; connection strings broken over lines).
5. final_rule2.py: the final list.

The rule: mask a provider's key format or a connection string, and each connection string's password alone (8+
characters, not a placeholder); a value with a key, token, secret or password name right before it; the form the text
holds for a value the scan read decoded; and, among the rest that are not an id, a hash or a piece of base64, every
value made only of key characters with 8+ distinct ones and at least one digit, but for 11 frequent ones read by hand as
ids. Left: ids, hashes, base64 pieces, code, paths, words, e-mail addresses, and 31 pieces of base64 in OpenHands
feedback that occur nowhere as written. First result: 373 values; its count pass showed two of them were common
text added as connection-string passwords (one 8-letter word in 68,393 places), so a password is masked alone only
when it is as random as a key (8+ distinct characters and a digit), a connection string with no password in it is
left, and five more frequent values read by hand as ids were left. Final: 319 values, 2,448 places in 172 sessions; no
value occurs over 100 times; the most frequent read by hand (a Cloud SQL password, an SSH password, user ids).
