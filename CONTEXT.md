# COI Formularze

Turns paper form blanks into JSON form configurations for e-services, and shows those configurations as the e-service a citizen would fill in.

## Language

**Form Definition**:
The JSON configuration of one e-service form (title, tabs, flow), as defined by the organisers' schema.
_Avoid_: output JSON, config, schema (the schema describes Form Definitions; it is not one)

**Tab**:
One page of the e-service; `MAIN` pages collect answers, the single last `FINISH` page shows the summary.
_Avoid_: step, screen

**Section**:
A titled card on a tab that groups fields; sections never nest.

**Repeating Section**:
A section the citizen can fill in several times (e.g. one instance per co-owner); its Answer is the list of its instances.

**Field**:
One component inside a section (text field, radio, file uploader, text block, …), identified by its tech name.

**State**:
A rule on a field or section that shows it or makes it read-only when its JSONata query over the current answers is true.
_Avoid_: condition, dependency (in code)

**Answer**:
The value a citizen has entered or chosen for a field during a fill-in session.

**Dictionary**:
A named, predefined list of options (Kraje, Województwo, …) that a select, radio or checkbox can use instead of its own options.

**Initial Data**:
A field value pre-filled from a registry (`PESEL`, `PESEL_ADDRESS`, `MLPZ` for the logged-in citizen), written as `<source>.<attribute>`; usually read-only, but some (contact phone, e-mail) may be changed by the citizen.
_Avoid_: default value (that is the JSON property, which also holds plain literals)

**Demo Data**:
Made-up dictionaries and a made-up citizen that the Form Preview uses to stand in for real Dictionaries and Initial Data; anything it doesn't cover is shown as a placeholder naming the source.

**Form Preview**:
A working, fillable rendering of a Form Definition in the browser: answers update live, states are re-evaluated, and tabs are navigated in flow order. Nothing is submitted.
_Avoid_: visualisation, mockup, static render
