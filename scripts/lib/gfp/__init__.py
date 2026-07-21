"""Offline GFP tenant-isolation benchmark library (ADR-017 serving boundary).

Benchmark-only code: lives under scripts/lib (never `fraudlens_ml`/`fraudlens_core`/
`backend`) and is consumed by explicit local commands. Nothing here may reach live scoring.
"""
