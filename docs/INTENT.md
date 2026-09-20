# Intent

Cauth lets one local user select among Claude accounts without repeating authorization
for every new session. The default workflow creates or imports long-lived setup-tokens,
stores them privately under aliases, and supplies the selected token to the official
Claude child process. Existing Claude processes keep their original token.

Selection, syntax validation, and account verification are distinct. Cauth should show
what it knows, avoid inventing identity or expiry information, and retain prior stored
state after failed or cancelled renewal. Verification requires a bounded real request.

The official command handles authorization and displays the generated token. Cauth
accepts a manual hidden paste afterward. Credentials belong in the user's private home
storage, outside the checkout. Shell integration contains no token.

Legacy browser-profile management remains available for compatibility, with atomic writes,
sync-back before swaps, and explicit recovery. Cauth is a local tool, not a credential
sharing service or a replacement for the official Claude client.
