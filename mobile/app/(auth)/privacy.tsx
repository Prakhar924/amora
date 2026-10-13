/**
 * Privacy Policy.
 *
 * Describes what this implementation actually does — in particular that
 * precise coordinates are stored but never disclosed, and that only a rounded
 * distance is ever sent to another user.
 */

import { LegalDocument } from '@/components/legal';

export default function Privacy() {
  return (
    <LegalDocument
      title="Privacy Policy"
      updated="13 October 2026"
      sections={[
        {
          heading: 'What we collect',
          body:
            'Your email address, a password (kept only as a bcrypt hash), your ' +
            'date of birth, the profile details and photos you choose to add, ' +
            'and — if you grant permission — your device location.',
        },
        {
          heading: 'Your location',
          body:
            'We store coordinates so we can work out who is near you, and we ' +
            'never disclose them. Other people see only your city and a distance ' +
            'rounded to the nearest kilometre. No API response contains your ' +
            'coordinates, not even your own profile.',
        },
        {
          heading: 'Your date of birth',
          body:
            'Used to confirm you are 18 or over, and then to derive the age ' +
            'shown on your profile. Other people see your age, never your date ' +
            'of birth.',
        },
        {
          heading: 'Your photos',
          body:
            'Photos are held in private object storage and served through ' +
            'short-lived signed links, so a copied link stops working. Every ' +
            'photo is reviewed by a moderator before it becomes visible.',
        },
        {
          heading: 'Your messages',
          body:
            'Messages are stored so your conversations survive across devices. ' +
            'They are readable only by you and the person you matched with. A ' +
            'moderator can see a particular message only if one of you reports it.',
        },
        {
          heading: 'Deleting your data',
          body:
            'Deleting your account removes your profile, photos, preferences, ' +
            'swipes, matches and messages, and replaces your email address with ' +
            'an unrecoverable placeholder. Reports filed about you are kept, so ' +
            'that someone cannot erase their history by deleting and rejoining.',
        },
        {
          heading: 'What we do not do',
          body:
            'We do not sell your data, show third-party advertising, or share ' +
            'your profile with data brokers.',
        },
      ]}
    />
  );
}
