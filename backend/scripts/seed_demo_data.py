"""Replace business data with the deterministic synthetic dataset.

Usage (from backend/): python -m scripts.seed_demo_data --customers 1000 --cases 500 --seed 20260902
"""

import argparse

from app.application.bootstrap import ensure_primary_organization
from app.infrastructure.database.seed import generate_synthetic_dataset
from app.infrastructure.database.session import SessionLocal, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--customers", type=int, default=1000)
    parser.add_argument("--cases", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20260902)
    args = parser.parse_args()

    init_db()
    with SessionLocal() as session:
        ensure_primary_organization(session)
        result = generate_synthetic_dataset(
            session, customer_count=args.customers, case_count=args.cases, seed=args.seed
        )
    print(f"Seeded {result['customers']} customers and {result['cases']} cases (seed={result['seed']}).")


if __name__ == "__main__":
    main()
