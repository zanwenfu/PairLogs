# Licences

This dataset has three parts, each under its own licence.

1. **Session text.** Each session's text keeps the licence of the place it came from.
   - Sessions from GitHub repositories keep their repository's licence. Each session row names it in `repo_license`
     (an SPDX id, as GitHub reads the repository). Tier A holds text only from repositories under a recognised open
     licence; text from a repository with no licence, or one GitHub cannot read, is not redistributed (tier B shares
     only pointers to it).
   - Sessions copied from DataClaw and from OpenHands feedback keep those datasets' MIT licence. Each tier's
     `changelog.json` credits every copied dataset it carries.
   - SWE-chat v2's sessions are not redistributed here.
2. **What the pipeline adds**: the labels, the pointers, the tables' layout and this documentation. These are under
   the Creative Commons Attribution 4.0 International licence (CC BY 4.0,
   https://creativecommons.org/licenses/by/4.0/). Please cite the dataset when you use them.
3. **The pipeline's code** is under the MIT licence.

Masking secrets and personal details is done with care but cannot be perfect. If you find a secret or personal detail,
or want a session of yours removed, write to zanwen.fu@duke.edu; it is removed from the next version.
