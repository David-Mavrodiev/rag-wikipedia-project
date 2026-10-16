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
    <div data-testid="answer-view" className={refused ? 'answer refused' : 'answer'}>
      <h3>{refused ? 'No answer' : 'Answer'}</h3>
      <p>{answer}</p>
      {refused && (
        <p className="note">
          The retrieved passages did not support an answer, so the system declined rather
          than inventing one.
        </p>
      )}
      {uncited && (
        <p data-testid="uncited-note" className="note warn">
          No source is cited for this answer, so it cannot be checked against the retrieved
          articles.
        </p>
      )}
    </div>
  )
}
