"""One-shot sample gate — what must be true before the app serves.

Two modes (SEED_MODE): `restore` loads the prebuilt corpus from demo_corpus/
(src/demo_restore.py) — what the shipped presets use; `ingest` indexes the
samples live (src/seeding.py) — the default for a stack on its own stores.

    python -m src.seed     # exits 0 when the samples are indexed (see below)

docker-compose runs this as a service the api + worker depend on via
`service_completed_successfully`, so the UI only becomes reachable once the
sample corpus is indexed. On Fly it's the [deploy] release_command.
Durable in Qdrant Cloud, so after the first successful run this exits in
seconds. Set SEED_SAMPLE_VIDEOS=false to skip the gate entirely.

Best-effort by default (SEED_STRICT=false): a seed that can't finish — e.g. a
fresh clone whose empty Qdrant forces a live YouTube re-index without cookies —
logs loudly and STILL exits 0, so the deploy proceeds and the app goes live with
an empty /demo instead of the whole deploy aborting. Set SEED_STRICT=true to make
an incomplete seed exit 1 (hard gate: never serve a half-indexed corpus).
"""
import sys

from . import config
from .seeding import seed_to_completion


def main() -> int:
    # Deploy-time gate: the release_command runs this first, so it's the earliest
    # place to catch a deploy carrying local settings. Warns (or aborts, under
    # STRICT_DEPLOY_CHECK) before the broken version would go live.
    from . import preflight
    preflight.check("seed / release_command")

    # SEED_MODE=restore: the samples were indexed once, offline, and ship as
    # data in demo_corpus/ — load them instead of re-indexing. It has its own
    # pass/fail (it verifies all three stores), so it returns straight through.
    if config.SEED_MODE == "restore":
        from . import demo_restore
        return demo_restore.main()

    if seed_to_completion():
        return 0
    if config.SEED_STRICT:
        print("[seed] incomplete and SEED_STRICT=true — aborting the deploy.", flush=True)
        return 1
    print("[seed] samples did not all index — continuing anyway (SEED_STRICT=false).\n"
          "       The app will start; /demo may be empty until the samples index.\n"
          "       Fix the cause (usually YouTube cookies / QDRANT_URL / DATABASE_URL)\n"
          "       and redeploy, set SEED_STRICT=true to make this abort instead, or\n"
          "       SEED_SAMPLE_VIDEOS=false to skip sample seeding entirely.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
