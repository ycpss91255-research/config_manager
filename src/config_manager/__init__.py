"""config_manager -- centralized config management.

Every module lives under this one package so that no top-level name can
shadow a standard-library one. `io` did: it is loaded during interpreter
startup to build sys.stdout, so an `io` package earlier on sys.path either
made itself unimportable or stopped the interpreter from booting at all
(ADR-00000026).
"""
