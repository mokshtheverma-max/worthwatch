# Examples

Everything here is about Mara Quinn, an invented person: she sells bookkeeping software to small
bakeries (Ledgerleaf) and writes a weekly newsletter for indie game makers (Tidepool).

- [sample-digest.md](sample-digest.md): one day's short digest, made by worthwatch's own code from
  invented videos (`python scripts/make_sample_digest.py` makes it again).
- Her starter files, which `worthwatch init` copies into your config folder for you to replace:
  - [context.md](../src/worthwatch/examples/context.md): who she is, her projects, how she works,
    what she has decided, the ideas she killed, and what each verdict means for her.
  - [settings.toml](../src/worthwatch/examples/settings.toml): her projects, her brain, her
    channels and the folders.
  - [rules.toml](../src/worthwatch/examples/rules.toml): her rules, which worthwatch checks in code
    after every write-up.
  - [channels.txt](../src/worthwatch/examples/channels.txt) and
    [topics.txt](../src/worthwatch/examples/topics.txt): what to check and what to search for.

To try worthwatch on her before writing your own, run `worthwatch init` and `worthwatch doctor`:
the doctor will remind you that the context file is still hers.
