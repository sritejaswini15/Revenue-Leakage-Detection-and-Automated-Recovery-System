"""Customer directory, import and Razorpay sync."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, DbSession, ManagerUser
from app.api.schemas.customers import (
    CustomerDetailOut,
    CustomerImportIn,
    CustomerList,
    CustomerOut,
    CustomerSyncIn,
    ImportSummaryOut,
)
from app.application import customers
from app.application.customers import ContactFilter, CustomerSort

router = APIRouter(tags=["customers"])


@router.get("/customers")
def list_customers(
    session: DbSession,
    user: CurrentUser,
    search: Annotated[str | None, Query(max_length=100)] = None,
    contact: ContactFilter = "all",
    sort: CustomerSort = "value",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerList:
    """Search by name, email, phone or Razorpay id. Sorted by lifetime value unless asked otherwise."""
    views, total = customers.list_customers(
        session, user.organization_id, search=search, contact=contact, sort=sort, limit=limit, offset=offset
    )
    return CustomerList(items=[CustomerOut.of(view) for view in views], total=total, limit=limit, offset=offset)


@router.get("/customers/{customer_id}", responses={404: {"description": "Customer not found"}})
def get_customer(customer_id: str, session: DbSession, user: CurrentUser) -> CustomerDetailOut:
    """Profile, combined payment statistics, recent payments and recovery cases."""
    return CustomerDetailOut.of_detail(customers.get_customer_detail(session, user.organization_id, customer_id))


@router.post("/customers/import", responses={400: {"description": "Unreadable file or missing columns"}})
def import_customers(payload: CustomerImportIn, session: DbSession, user: ManagerUser) -> ImportSummaryOut:
    """Preview (commit=false) or apply a CSV import. Invalid and duplicate rows are never written."""
    return ImportSummaryOut.model_validate(customers.import_csv(session, user, payload.csv, payload.commit))


@router.post("/customers/sync", responses={400: {"description": "No Razorpay credentials for this merchant"}})
def sync_customers(payload: CustomerSyncIn, session: DbSession, user: ManagerUser) -> ImportSummaryOut:
    """Preview or apply the merchant's customer list from Razorpay, fetched with its own keys."""
    return ImportSummaryOut.model_validate(customers.sync_from_razorpay(session, user, payload.commit))
