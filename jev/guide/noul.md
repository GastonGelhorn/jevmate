# Noul: the probability that a statement is true
live: /primitives/noul

    jev yes 'Does `text` ask for money back?' --field text=@mail.txt          # 0.83 yes, exit 0
    jev yes '…' --true "an explicit request for a refund or chargeback" --false "a complaint without a request" -s @mail.txt
    jev yes '…' --band 0.35 0.65 -s @mail.txt                                  # yes | uncertain | no

The answer is `{"noul": p}`. It is a probability, not a score: 0.5 is "could go either way", and a
calibrated 0.8 means that of all the times the model says 0.8, about eight in ten are yes.

Criteria (`true=`, `false=`) describe what each side means. They matter most near the boundary:
write the near miss into the side it belongs to ("a complaint without a request" is false).

Exit codes make it usable in a shell `if`: 0 when p >= the threshold (or the top of the band), 1
otherwise. In Python, `decide()` returns a `Decision` with three outcomes (`jev guide library`).

Two nouls that should be complementary ("is it X?", "is it not X?") will not sum to one. Ask the
question you want, one way, and pick the threshold with `jev tune`.
