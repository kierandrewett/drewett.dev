---
title: Notes about Rust on the server
date: 2026-05-18
summary: Why this site is a single static-feeling Axum binary instead of anything fancier.
---

I keep coming back to the same setup: one Rust binary, Axum routes, Tera
templates, a thin caching layer over a few markdown files in the repo.

It's not the fastest thing in the world, but it deploys as a single
container, has no JS framework to upgrade, and the cold-start feels closer
to a static site than a CMS. Everything I'd want to change lives in a
markdown file or a `.css` file.

The trick is just to keep saying "no" to extra moving parts.
