interface Props {
  answer: string
}

export default function AnswerView({ answer }: Props) {
  return (
    <div
      data-testid="answer-view"
      style={{ margin: '1rem 0', padding: '1rem', background: '#f5f5f5' }}
    >
      <h3>Answer</h3>
      <p>{answer}</p>
    </div>
  )
}
