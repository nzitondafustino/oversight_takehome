"""Run: python -m expense_validator.classification.evaluate. Kept only as this command: see evaluation/cli/classification.py."""

if __name__ == "__main__":
    import sys

    from expense_validator.evaluation.cli.classification import main

    sys.exit(main())
