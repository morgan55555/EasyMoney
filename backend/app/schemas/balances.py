"""Balance response schemas (AGENTS.md §2.2, §2.3, §2.12).

Money is always a string in the API (§2.12), rendered with the relevant
currency's minor digits by ``format_money``.
"""

from __future__ import annotations

from pydantic import BaseModel


class MemberBalanceOut(BaseModel):
    user_id: int
    display_name: str
    balance: str


class TransferOut(BaseModel):
    from_user_id: int
    to_user_id: int
    amount: str


class GroupBalancesOut(BaseModel):
    currency: str
    balances: list[MemberBalanceOut]
    # "Current pairwise view" (§2.4): every debtor -> creditor amount.
    pairs: list[TransferOut]
    # The §2.3 greedy suggestion: at most n-1 transfers settling all debts.
    transfers: list[TransferOut]


class CurrencyBalanceOut(BaseModel):
    currency: str
    balance: str
