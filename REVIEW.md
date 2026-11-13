# Review instructions

## What Important means here

Breaks behavior, leaks data or secrets, or blocks a rollback. Style and naming
are a nit at most.

## Cap the nits

At most 3 nits per review.

## Do not report

Anything CI enforces. Generated files, lockfiles, and vendored code. This
repository does not vendor upstream skills.

## Always check

New entry points have a test. Logs have no secrets. Workflow files do not
gain write permissions. No merge tool is enabled.

## Evidence

Every behavior claim cites a file and line in the source.

## Summary

Start with the count of findings by tag. Say "Nothing to fix" when there is none.
