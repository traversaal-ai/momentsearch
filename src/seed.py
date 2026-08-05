"""One-shot sample seeder — the startup gate.

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
