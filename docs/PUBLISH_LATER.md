# Create and push later — instructions only

No GitHub repository was created, no remote is configured and nothing was pushed/published by this preparation.

The [license and attribution review](REVIEW_BEFORE_PUBLICATION.md) records Cody’s confirmation and GPL-3.0-only decision. When Cody separately authorizes publication:

1. Sign into the intended GitHub account/organization and create **CBI_RenderRepo** as an empty repository. Do not initialize it with a README, license or gitignore: those are already in the clean initial commit.
2. Review the local commit and author placeholder. The approved current identity is Chippie Bear Industries, LLC, `maintainers@cbi-renderrepo.invalid`; that address is intentionally non-contact. If changing it, explicitly set the desired repository-local identity and amend the unpublished initial commit before continuing.
3. In the local CBI_RenderRepo directory, inspect `git status`, `git log --oneline` and `git remote -v`. There should be one root commit and no remote. Substitute the real owner in these **future** commands:

   ```bash
   git remote add origin https://github.com/YOUR_OWNER/CBI_RenderRepo.git
   git push -u origin main
   ```

4. Verify the exact commit and source/setup/workflow files anonymously from the public URL, then rehearse setup from a new public clone. Review any branch protections or visibility settings yourself.

These instructions are not authorization to execute them now. Repository publication, video publication, social posting, contest agreements and final submission remain separate actions. The source ZIP excludes .git storage; use the prepared local repository to retain the clean initial commit, or initialize the extracted ZIP yourself later if needed.
