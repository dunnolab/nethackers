"""``nethackers setup``: get a machine ready in one command (see ``flow``).

Deliberately imports nothing: doctor (``diagnostics``) imports the OS files
(``setup.macos`` / ``setup.linux``) for its fix hints, and importing this
package must never drag in ``setup.flow``, which imports ``diagnostics``."""
