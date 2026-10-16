interface Props {
  onPick: (question: string) => void
  disabled?: boolean
}

/**
 * Three questions that show the three behaviours worth seeing, in order.
 *
 * They are not decoration. A visitor who types a vague question ("Tell me about
 * X") meets the measured weakness - the model refuses 35% of those even with
 * the right article retrieved - and leaves thinking the system does not work.
 * These put the honest failure where it is explained rather than where it is
 * mistaken for a bug.
 */
const EXAMPLES: { question: string; why: string }[] = [
  {
    question: 'What is the Fuji T-3?',
    why: 'Answers from one article, with the source shown.',
  },
  {
    question:
      'Which company bought the Spanish-language station out of the Pappas bankruptcy for $4 million?',
    why: 'A detail that never names its article: retrieval has to find it.',
  },
  {
    question: 'What is the AIM-120 AMRAAM?',
    why: 'Outside the corpus: the system declines instead of inventing.',
  },
]

export default function ExampleQuestions({ onPick, disabled = false }: Props) {
  return (
    <div className="examples" data-testid="example-questions">
      {EXAMPLES.map((example) => (
        <button
          key={example.question}
          type="button"
          className="example"
          disabled={disabled}
          onClick={() => onPick(example.question)}
        >
          {example.question}
          <span className="why">{example.why}</span>
        </button>
      ))}
    </div>
  )
}
