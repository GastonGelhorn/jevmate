# Choice: one option out of a fixed set
live: /primitives/choice

    jev pick 'Which team should handle `text`?' billing="charges, invoices, refunds" technical="bugs, outages" other -s @t.txt
    jev pick '…' a b c --min-confidence 0.6 -s @t.txt      # exit 1 when the distribution is flat

The answer is `{"choice", "probabilities": {option: p}, "confidence"}`. `choice` is the argmax;
`confidence` says how peaked the distribution is. A confident wrong answer and an unconfident
right one look different here, which is the point: gate on confidence, and treat a runner-up
with real mass as a signal that the option list is missing a boundary or an `other`.

Describe each option so it is distinct from its neighbour. Options may be a bare NAME when the
name says it all, NAME=description otherwise, up to 255 of them. The list is sent whole every
time; the model does not remember it.

Do not carry a threshold from a noul to a choice: 0.6 confidence over three options and 0.6 over
forty options mean different things. Measure with labelled rows (`jev tune` is built for nouls;
for a choice, turn the decision you act on into a noul, or check the argmax against labels in a
script).
