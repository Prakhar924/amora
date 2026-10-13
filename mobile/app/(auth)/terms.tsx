/**
 * Terms of Service.
 *
 * Placeholder product copy. It states the rules this implementation actually
 * enforces (18+, no harassment, moderation, deletion) rather than pretending
 * to be a lawyer-reviewed document. Replace before launch — see TODO.md.
 */

import { LegalDocument } from '@/components/legal';

export default function Terms() {
  return (
    <LegalDocument
      title="Terms of Service"
      updated="13 October 2026"
      sections={[
        {
          heading: 'Who may use Amora',
          body:
            'You must be at least 18 years old. We check the date of birth you ' +
            'give at registration and refuse accounts below that age. If we find ' +
            'that an account belongs to someone under 18, we remove it.',
        },
        {
          heading: 'Your account',
          body:
            'Keep your password private and your profile truthful. One person, ' +
            'one account. Do not impersonate anyone else, and do not use photos ' +
            'that are not of you.',
        },
        {
          heading: 'How to behave',
          body:
            'No harassment, hate speech, threats, sexual content involving ' +
            'minors, solicitation, spam or scams. Only people you have matched ' +
            'with can message you, and you can unmatch or block anyone at any ' +
            'time, without giving a reason.',
        },
        {
          heading: 'Moderation',
          body:
            'Photos are reviewed before they appear publicly. Reports are read ' +
            'by a moderator, who may suspend or remove an account. We also limit ' +
            'how often you can swipe, message and report, in order to curb abuse.',
        },
        {
          heading: 'Ending your use',
          body:
            'You can delete your account at any time from Settings. Deletion is ' +
            'permanent: your profile, photos and messages are removed. Records of ' +
            'reports made about you are kept for safety reasons.',
        },
        {
          heading: 'No warranty',
          body:
            'Amora is provided as is. We do not run criminal background checks ' +
            'and we do not verify anyone’s identity. Use your judgement, meet in ' +
            'public, and tell someone where you are going.',
        },
      ]}
    />
  );
}
