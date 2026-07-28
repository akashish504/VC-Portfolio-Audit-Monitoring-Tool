import { OktaAuth } from '@okta/okta-auth-js';
import {
  ENABLE_OKTA,
  OKTA_CLIENT_ID,
  OKTA_ISSUER,
  OKTA_POST_LOGOUT_REDIRECT_URI,
  OKTA_REDIRECT_URI,
} from '../api/config';

const issuer = (OKTA_ISSUER || '').trim();
const clientId = (OKTA_CLIENT_ID || '').trim();

const redirectUri =
  (OKTA_REDIRECT_URI || '').trim() ||
  (typeof window !== 'undefined' && window.location?.origin
    ? `${window.location.origin}/login/callback`
    : '');

const postLogoutRedirectUri =
  (OKTA_POST_LOGOUT_REDIRECT_URI || '').trim() ||
  (typeof window !== 'undefined' && window.location?.origin ? window.location.origin : '');

/**
 * Single shared instance (see portfolio-review-app-ui-master). `null` when Okta is disabled or
 * issuer/client are missing.
 */
export const oktaAuth: OktaAuth | null =
  ENABLE_OKTA && issuer && clientId
    ? new OktaAuth({
        issuer,
        clientId,
        redirectUri,
        postLogoutRedirectUri,
        scopes: ['openid', 'profile', 'email'],
        pkce: true,
        tokenManager: {
          storage: 'sessionStorage',
          autoRenew: true,
          secure: false,
        },
      })
    : null;

export function getOktaAuth(): Promise<OktaAuth> {
  if (!ENABLE_OKTA || !oktaAuth) {
    return Promise.reject(new Error('Okta disabled or not configured'));
  }
  return Promise.resolve(oktaAuth);
}
