"""Read-only: typed rows (copies left out) with our pushback label and with SWE-chat's, in each version given
(NAME=DIR ...)."""
import sys

import duckdb

for arg in sys.argv[1:]:
    name, V = arg.split("=", 1)
    con = duckdb.connect()
    con.execute("SET threads=16; SET memory_limit='56GB'; SET temp_directory='/path/to/pairlogs-work/tmp-p28'")
    r = con.execute(f"""select count(*), count(pushback), count(prompt_pushback), count_if(pushback) from read_parquet(
      '{V}/conversations/*/*.parquet', union_by_name=true) where typed_by_developer and copy_of is null""").fetchone()
    print(name, "typed rows", r[0], "with our pushback label", r[1], "with SWE-chat's", r[2], "pushback", r[3], flush=True)
