"""Layer io: everything outside the process.

Parsers, the git CLI wrapper, the atomic writer. Dependency direction is
api -> core -> io, one way; core never imports api.

This file existing at all is the point of ADR-00000026: at src/io/ it would
have stopped the interpreter from starting.
"""
