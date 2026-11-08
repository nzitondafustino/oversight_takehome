"""Run: python -m expense_validator.classification.robustness --check | --run NAME. Kept only as this command: see evaluation/cli/robustness.py."""

if __name__ == "__main__":
    import sys

    from expense_validator.evaluation.cli.robustness import main

    sys.exit(main())
