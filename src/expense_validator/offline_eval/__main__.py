"""Run: python -m expense_validator.offline_eval [--predictions FILE] [--only A H0]. See evaluation/cli/policy.py."""

if __name__ == "__main__":
    import sys

    from expense_validator.evaluation.cli.policy import main

    sys.exit(main())
