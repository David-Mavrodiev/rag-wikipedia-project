const DOCS =
  'https://github.com/David-Mavrodiev/rag-wikipedia-project/blob/main/projects/rag-wikipedia/docs/measurement-baseline.md'

/** Published figures, copied from the committed reports the link points at. */
const PUBLISHED: { label: string; value: string; note: string }[] = [
  {
    label: 'Retrieval finds the right article',
    value: '0.950',
    note: 'recall@5 on 60 title-free questions over the served corpus',
  },
  {
    label: 'Wrongly accepts a question it cannot answer',
    value: '0.800 → 0.150',
    note: 'the gate lets 16 of 20 through; the model refuses 13 of those, so 3 reach a user',
  },
  {
    label: 'Refuses a question it could have answered',
    value: '0.133 / 0.350',
    note: 'specific questions / vague ones ("Tell me about X"), with the right article retrieved',
  },
  {
    label: 'Claims supported by the retrieved text',
    value: '0.792 judged, 51 of 53 by hand',
    note: 'the judge over-flags: 8 of its 11 flags were correct answers it could not verify',
  },
  {
    label: 'Answer latency',
    value: '≈4 s warm, 37 s cold',
    note: 'measured on this deployment; the GPU sleeps when nobody is asking',
  },
]

const LESSONS: { head: string; body: string }[] = [
  {
    head: 'The number came before the fix',
    body: 'Every figure above was measured before anything was improved, and the ones that fail are published next to the ones that pass. A pilot that reports only its wins cannot be trusted with the next one.',
  },
  {
    head: 'Refusing is the feature',
    body: 'In a regulated or industrial setting a confident wrong answer costs more than no answer. This system declines when the corpus cannot support a claim — and the two fabrications in 125 claims both came from questions it should have refused, which is where the work goes next.',
  },
  {
    head: 'The evaluator is measured too',
    body: 'The automatic groundedness judge was checked against 110 hand-labelled claims: it agrees 82% of the time and four of five of its alarms are false. Knowing that is the difference between a metric and a decoration.',
  },
  {
    head: 'A measurement belongs to one deployment',
    body: 'The live check above refuses to show the committed audit, because that audit scored a different corpus. Numbers that travel without their conditions are how a demo passes and production fails.',
  },
  {
    head: 'Cost is part of the design',
    body: 'Idle costs nothing: the GPU scales to zero, one container at a time, and a daily ceiling stops the whole thing if spending moves. A deployment nobody can afford to leave running is not deployed.',
  },
]

/**
 * The page's own explanation of its two measurements.
 *
 * A visitor sees a live audit that says "suspect_overfit" and a published set of
 * numbers that look worse than a demo would normally admit. Without this, both
 * read as faults. With it, they read as the point.
 */
export default function HowItIsMeasured() {
  return (
    <section className="measured" aria-labelledby="measured-title" data-testid="how-measured">
      <h2 id="measured-title">How this system is measured, and why both numbers are shown</h2>

      <div className="measured-grid">
        <div className="measured-card">
          <h3>Live check — this container, right now</h3>
          <p className="note">
            Press <em>Run audit</em> below: it scores the evaluation suites against the corpus
            this deployment is actually serving and returns in about four seconds. It is red
            today, and that is the honest state, not a staging artefact.
          </p>
        </div>
        <div className="measured-card">
          <h3>Published measurement — held-out questions</h3>
          <p className="note">
            The deeper run: title-free questions the system has never been tuned on, every
            answer judged claim by claim, with the conditions recorded.{' '}
            <a href={DOCS}>Read the full report</a>.
          </p>
          <dl className="published">
            {PUBLISHED.map((item) => (
              <div key={item.label}>
                <dt>
                  {item.label} <strong>{item.value}</strong>
                </dt>
                <dd>{item.note}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      <h3 className="lessons-title">What this is meant to demonstrate</h3>
      <ul className="lessons">
        {LESSONS.map((lesson) => (
          <li key={lesson.head}>
            <strong>{lesson.head}.</strong> {lesson.body}
          </li>
        ))}
      </ul>
    </section>
  )
}
