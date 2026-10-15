interface Props {
  answer: string
  refused?: boolean
  // From the API: at least one citation resolves to a retrieved chunk.
  cited?: boolean
}

export default function AnswerView({ answer, refused = false, cited = true }: Props) {
  // An uncited answer is still shown - it may well be right - but the reader is
  // told it cannot be checked against a source. Before this, an answer ending
  // in a literal "[n]" looked exactly like a sourced one.
  const uncited = !refused && !cited
  return (
    <div
      data-testid="answer-view"
      style={{ margin: '1rem 0', padding: '1rem', background: '#f5f5f5' }}
    >
      <h3>Answer</h3>
      <p>{answer}</p>
      {uncited && (
        <p data-testid="uncited-note" style={{ color: '#8a5a00', fontSize: '0.9em' }}>
          No source is cited for this answer, so it cannot be checked against the retrieved
          articles.
        </p>
      )}
    </div>
  )
}
