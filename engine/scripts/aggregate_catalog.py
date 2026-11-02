#!/usr/bin/env python3
"""Variant-aware Shopify public RETAIL catalog summary, never wholesale/sales inference."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path


def aggregate(payload, *, source_sha256=None):
    rows = payload.get('products')
    if not isinstance(rows, list):
        raise ValueError('Expected products array')
    seen_p = set(); seen_v = set(); groups = {}
    duplicate_products = 0; duplicate_variants = 0; zero_price = 0; invalid_price = 0
    prices = []; variant_count = 0
    for product in rows:
        if not isinstance(product, dict):
            raise ValueError('Product entry must be an object')
        pid = str(product.get('id') or product.get('handle') or '').strip()
        if not pid:
            raise ValueError('Product identifier is missing')
        if pid in seen_p:
            duplicate_products += 1
            continue
        seen_p.add(pid)
        kind = str(product.get('product_type') or 'Uncategorized').strip() or 'Uncategorized'
        stats = groups.setdefault(kind, {'products': 0, 'variants': 0, 'valid_retail_prices': [], 'zero_price_variants': 0})
        stats['products'] += 1
        for variant in product.get('variants') or []:
            vid = str(variant.get('id') or '').strip()
            if vid:
                if vid in seen_v:
                    duplicate_variants += 1
                    continue
                seen_v.add(vid)
            variant_count += 1
            stats['variants'] += 1
            try:
                price = Decimal(str(variant['price']))
            except (KeyError, ValueError, InvalidOperation, TypeError):
                invalid_price += 1
                continue
            if not price.is_finite() or price < 0:
                invalid_price += 1
            elif price == 0:
                zero_price += 1
                stats['zero_price_variants'] += 1
            else:
                prices.append(price)
                stats['valid_retail_prices'].append(price)
    def fmt(v):
        return str(v.quantize(Decimal('0.01'))) if v is not None else None
    by_type = []
    for type_, group in sorted(groups.items(), key=lambda item: (-item[1]['products'], item[0])):
        ps = group['valid_retail_prices']
        by_type.append({'product_type': type_, 'product_count': group['products'], 'variant_count': group['variants'],
                        'nonzero_retail_min': fmt(min(ps)) if ps else None, 'nonzero_retail_max': fmt(max(ps)) if ps else None,
                        'nonzero_retail_avg_per_variant': fmt(sum(ps) / len(ps)) if ps else None, 'zero_price_variant_count': group['zero_price_variants']})
    return {
        'basis': 'public Shopify products.json / variants[].price only; currency and tax locale require separate verification',
        'source_sha256': source_sha256, 'product_record_count': len(rows), 'distinct_product_count': len(seen_p),
        'distinct_variant_count': variant_count, 'duplicate_product_rows_skipped': duplicate_products,
        'duplicate_variants_skipped': duplicate_variants,
        'zero_price_variant_count': zero_price, 'invalid_price_variant_count': invalid_price,
        'retail_nonzero_min': fmt(min(prices)) if prices else None, 'retail_nonzero_max': fmt(max(prices)) if prices else None,
        'retail_nonzero_avg_per_variant': fmt(sum(prices) / len(prices)) if prices else None,
        'group_by_raw_product_type': by_type, 'complete_catalog_proven': False,
        'disclaimer': 'These are catalog observations, not SKU purchase records, revenue, wholesale quotes, conversion rates or verified demand.'
    }


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--input', required=True); ap.add_argument('--out', required=True)
    args = ap.parse_args()
    raw = Path(args.input).read_bytes()
    result = aggregate(json.loads(raw), source_sha256=hashlib.sha256(raw).hexdigest())
    result['computed_at_utc'] = datetime.now(timezone.utc).isoformat()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'products': result['distinct_product_count'], 'variants': result['distinct_variant_count'], 'zero_price': result['zero_price_variant_count']}))

if __name__ == '__main__': main()
