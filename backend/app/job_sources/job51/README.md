# job51 adapter (planned, not implemented)

Placeholder for a future `JobSource` implementation. Nothing here yet - by design.

When this is built (target: v0.3) it must obey the rules in
`backend/app/job_sources/base.py`:

- the **user** logs in interactively in a visible browser; the app never stores
  or transmits recruitment-site credentials;
- **no** CAPTCHA solving, **no** anti-bot evasion, **no** stealth fingerprinting;
- honour the site's rate limits, robots policy and terms of service;
- collection only. Applying and messaging stay behind explicit human approval
  (`AUTO_APPLY` is false and there is no code path that flips it in v0.1).

If any of the above cannot be satisfied for this site, the adapter should not
be written at all.
