#!/usr/bin/env python3
"""Django's command line runner; defaults to hub.settings.api, commands pass --settings."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hub.settings.api")
    from django.core.management import execute_from_command_line  # noqa: PLC0415

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
