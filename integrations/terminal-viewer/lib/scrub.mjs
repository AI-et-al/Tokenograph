export function scrub(text){return String(text??'').replace(/sk-[A-Za-z0-9_-]{15,}/g,'[REDACTED KEY]').replace(/Bearer\s+[A-Za-z0-9._-]{20,}/gi,'Bearer [REDACTED]');}
