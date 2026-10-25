/**
 * journey.js: what ONE real user does. hold.js and staircase.js import it, so every test uses the same user.
 *
 * This is an example for a small shop. Replace the journeys, endpoints, think times and weights with the ones in
 * loadtest/PROFILE.md. Keep the shape:
 *   - ENDPOINTS lists every `name` tag used below (lib.js reports each one separately)
 *   - default() runs ONE iteration: picks a journey by weight and runs it with human pauses (think())
 *   - every request has tags: {name} and a check() on its status
 *
 * Profile this example was written for: ~0.11 req/s per user; 88 % reads / 12 % writes;
 * journeys browse 70 % / review 25 % / buy 5 %; think time p25–p75 ≈ 4–12 s.
 */
import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';
import { env, envNum, think, pickWeighted } from './lib.js';

const BASE = env('BASE_URL', '');

export const ENDPOINTS = [
  'GET /', // the page document (SSR)
  'GET /api/products',
  'GET /api/products/:id',
  'GET /api/products/:id/reviews',
  'POST /api/products/:id/reviews',
  'POST /api/cart',
  'POST /api/checkout',
];

// Test accounts: [{"email": "...", "token": "..."}], made for the load test (never a real person's account).
// One per VU when the app rate-limits per user. LG_INDEX * ACCOUNT_STRIDE keeps generators on different accounts.
const accounts = new SharedArray('accounts', () => {
  const file = env('ACCOUNTS_FILE', '');
  return file ? JSON.parse(open(file)) : [];
});
function account() {
  if (!accounts.length) return null;
  return accounts[(__VU - 1 + envNum('LG_INDEX', 0) * envNum('ACCOUNT_STRIDE', 100000)) % accounts.length];
}
function authed(a, name) {
  return { headers: { Authorization: `Bearer ${a.token}`, 'Content-Type': 'application/json' }, tags: { name } };
}

// Opening the product list is what the browser does on landing: the HTML, then the list API (in parallel with
// nothing else here; use http.batch when the page fires several API calls at once).
function landing() {
  const page = http.get(`${BASE}/`, { tags: { name: 'GET /' }, responseType: 'none' });
  check(page, { 'home 200': (r) => r.status === 200 });
  const list = http.get(`${BASE}/api/products?page=1`, { tags: { name: 'GET /api/products' } });
  const products = list.status === 200 ? list.json('items') : null;
  check(list, { 'products 200': (r) => r.status === 200, 'products non-empty': () => Array.isArray(products) && products.length > 0 });
  return products || [];
}

// Opening a product fires two requests together, so they go out as one batch.
function openProduct(products) {
  const p = products[Math.floor(Math.random() * products.length)];
  const [detail, reviews] = http.batch([
    ['GET', `${BASE}/api/products/${p.id}`, null, { tags: { name: 'GET /api/products/:id' }, responseType: 'none' }],
    ['GET', `${BASE}/api/products/${p.id}/reviews`, null, { tags: { name: 'GET /api/products/:id/reviews' }, responseType: 'none' }],
  ]);
  check(detail, { 'product 200': (r) => r.status === 200 });
  check(reviews, { 'reviews 200': (r) => r.status === 200 });
  return p;
}

const JOURNEYS = [
  {
    name: 'browse', weight: 70,
    run() {
      const products = landing();
      if (!products.length) return think(3, 8);
      think(3, 8);
      for (let i = 0, n = 1 + Math.floor(Math.random() * 3); i < n; i++) {
        openProduct(products);
        think(5, 15);
      }
    },
  },
  {
    name: 'review', weight: 25,
    run() {
      const a = account();
      const products = landing();
      if (!products.length || !a) return think(3, 8);
      think(3, 8);
      const p = openProduct(products);
      think(10, 30); // writing a review takes a while
      const body = JSON.stringify({ rating: 1 + Math.floor(Math.random() * 5), text: `loadtest-review VU ${__VU} iter ${__ITER}` });
      const res = http.post(`${BASE}/api/products/${p.id}/reviews`, body, authed(a, 'POST /api/products/:id/reviews'));
      check(res, { 'review 201': (r) => r.status === 201 });
      think(3, 8);
    },
  },
  {
    name: 'buy', weight: 5,
    run() {
      const a = account();
      const products = landing();
      if (!products.length || !a) return think(3, 8);
      think(3, 8);
      const p = openProduct(products);
      think(5, 15);
      const cart = http.post(`${BASE}/api/cart`, JSON.stringify({ productId: p.id, qty: 1 }), authed(a, 'POST /api/cart'));
      check(cart, { 'cart 200': (r) => r.status === 200 });
      think(5, 20);
      // Agreed in PROFILE.md: staging uses the payment provider's test mode, so checkout is safe to call.
      const pay = http.post(`${BASE}/api/checkout`, JSON.stringify({ test: true }), authed(a, 'POST /api/checkout'));
      check(pay, { 'checkout 200': (r) => r.status === 200 });
      think(5, 10);
    },
  },
];

// Optional: runs once before the test. Return value is passed to every iteration as `data`.
export function setup() {
  const res = http.get(`${BASE}/`);
  if (res.status !== 200) throw new Error(`${BASE}/ returned ${res.status}; is BASE_URL right and the site up?`);
  return {};
}

export default function () {
  if (__ITER === 0) think(0, 5); // so VUs that start together don't stay in lockstep
  pickWeighted(JOURNEYS).run();
  think(5, 15); // idle before the next visit/action
}
