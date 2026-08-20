import { oauthUrl } from '../auth'

interface Props {
  disabled: boolean
}

// Plain links, not buttons: the OAuth handshake is a full-page navigation to
// the provider and back, not an XHR. They borrow the secondary button styling
// so they do not read as stray hyperlinks next to the real buttons.
export default function OAuthButtons({ disabled }: Props) {
  return (
    <>
      <p className="divider">or</p>
      <div className="oauth">
        <a
          className="button-secondary"
          href={disabled ? undefined : oauthUrl('google')}
          aria-disabled={disabled}
          data-testid="oauth-google"
        >
          Continue with Google
        </a>
        <a
          className="button-secondary"
          href={disabled ? undefined : oauthUrl('github')}
          aria-disabled={disabled}
          data-testid="oauth-github"
        >
          Continue with GitHub
        </a>
      </div>
    </>
  )
}
