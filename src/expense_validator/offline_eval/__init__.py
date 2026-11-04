"""Offline evaluation: how classification quality carries through to policy results and dispositions.

This reads the engine and changes none of its semantics. It makes no network call. Answers from the
dataset are used only to score, and never reach the engine. Every set of classifications says where it
came from, and only those of kind `actual` are ever reported as measured classifier performance.
"""
