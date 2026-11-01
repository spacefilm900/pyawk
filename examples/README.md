# Examples

Three small AWK programs, each with sample input. Every `.awk` file starts
with a comment explaining what the program does and how AWK runs it.
The comments cover the implicit loop over input lines, the fields `$1`..`$NF`,
`NR`, arrays and the `END` block.

Run them from the repository root:

| Program | What it does | Command |
|---|---|---|
| `sum.awk` | For each line, adds up the fields that are numbers and prints `line N: total` | `python -m pyawk -f examples/sum.awk examples/sum.in` |
| `medals.awk` | Reports each word containing `GOLD` (WINNER) or `SILVER` (RUNNER-UP), with its line number | `python -m pyawk -f examples/medals.awk examples/medals.in` |
| `reverse.awk` | Prints the lines last to first, with the words on each line reversed too | `python -m pyawk -f examples/reverse.awk examples/reverse.in` |

Each program produces exactly the same output under a system awk. Check it with:

```sh
for f in sum medals reverse; do
  diff <(python -m pyawk -f examples/$f.awk examples/$f.in) \
       <(awk -f examples/$f.awk examples/$f.in) && echo "$f: same"
done
```
