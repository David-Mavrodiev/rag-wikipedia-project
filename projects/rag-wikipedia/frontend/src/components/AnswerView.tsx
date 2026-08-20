interface Props {
  answer: string
}

export default function AnswerView({ answer }: Props) {
  return (
    <div className="card" data-testid="answer-view">
      <h3>Answer</h3>
      <p className="answer">{answer}</p>
    </div>
  )
}
