# Chromium sandbox profile

`chromium-seccomp.json` is copied without changes from
[Playwright v1.60.0](https://github.com/microsoft/playwright/blob/v1.60.0/utils/docker/seccomp_profile.json).
It uses Docker's default deny policy and permits the namespace operations that
Chromium needs for its own sandbox. The upstream Apache 2.0 licence is included
as `PLAYWRIGHT-LICENSE`.

Use this profile with the image's non-root user, `--init`, and `--shm-size=1g`.
Do not disable Chromium's sandbox to fix a host configuration problem.
