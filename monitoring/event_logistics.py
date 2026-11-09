"""Event desk operations: seating and audited pizza/drink ticket redemption.

Every mutation runs inside a database transaction. An operation ID makes repeated
browser requests idempotent. Staff only receive the minimum participant information
needed to locate a team and honor a ticket; registration health/contact details do
not appear in this endpoint.
"""
from sqlalchemy import select

from .attendance import EventRoster
from .db import (Audit, EventMember, FoodDrinkBalance, FoodDrinkTransaction,
                 Registration, TeamSeat, now)
from .service import WorkflowError


class EventLogistics:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def _staff(session, actor, organiser=False):
        if not organiser:
            member = session.get(EventMember, int(actor))
            if not member or member.group != 'monitors':
                raise WorkflowError('Only event monitoring staff may use the reception desk.')

    @staticmethod
    def _team(session, registration_id):
        registration = session.get(Registration, str(registration_id or ''))
        if not registration:
            raise WorkflowError('The competition team is not registered.')
        return registration

    @staticmethod
    def _email(reg, email):
        value = str(email or '').strip().casefold()
        if not value or value not in {
            str(member.get('email') or '').strip().casefold()
            for member in (reg.members or [])
        }:
            raise WorkflowError('The participant is not registered for this team.')
        return value

    def overview(self, actor, organiser=False):
        with self.sessions() as session:
            self._staff(session, actor, organiser)
            seats = {r.registration_id: r.table_number
                     for r in session.scalars(select(TeamSeat))}
            balances = {(r.registration_id, r.member_email, r.kind): r
                        for r in session.scalars(select(FoodDrinkBalance))}
            results = []
            summary = {'teams': 0, 'participants': 0,
                       'pizza_issued': 0, 'pizza_redeemed': 0,
                       'drink_issued': 0, 'drink_redeemed': 0}
            for reg in session.scalars(select(Registration).order_by(Registration.team_name)):
                people = []
                for member in reg.members or []:
                    email = str(member.get('email') or '').strip().casefold()
                    if not email:
                        continue
                    entry = {'name': member.get('name') or email, 'email': email}
                    for kind in ('pizza', 'drink'):
                        ledger = balances.get((reg.id, email, kind))
                        entry[kind] = {
                            'issued': ledger.issued if ledger else 0,
                            'redeemed': ledger.redeemed if ledger else 0,
                        }
                        summary[kind + '_issued'] += entry[kind]['issued']
                        summary[kind + '_redeemed'] += entry[kind]['redeemed']
                    people.append(entry)
                summary['participants'] += len(people)
                summary['teams'] += 1
                results.append({
                    'id': reg.id, 'name': reg.team_name,
                    'table_number': seats.get(reg.id, ''),
                    'members': people,
                })
            return {'teams': results, 'summary': summary}

    def set_table(self, actor, registration_id, table_number, organiser=False):
        number = str(table_number or '').strip()
        if number and (not number.isascii() or not number.isdecimal()
                       or not 1 <= int(number) <= 9999):
            raise WorkflowError('Table number must be an integer between 1 and 9999.')
        with self.sessions.begin() as session:
            self._staff(session, actor, organiser)
            reg = self._team(session, registration_id)
            if not organiser and EventRoster._assigned_id(session, reg) != int(actor):
                raise WorkflowError('Only the assigned monitor can change this team table.')
            seat = session.get(TeamSeat, reg.id)
            if seat and seat.table_number == number:
                return False
            if seat is None:
                seat = TeamSeat(registration_id=reg.id)
                session.add(seat)
            previous = seat.table_number
            seat.table_number = number
            seat.actor_id = int(actor)
            seat.updated_at = now()
            session.add(Audit(
                team_id=reg.team_id or 0, batch_id='', actor_id=int(actor),
                action='team_table_changed', details={
                    'registration_id': reg.id, 'previous': previous,
                    'table_number': number,
                }
            ))
            return True

    def ticket_action(self, actor, operation_id, registration_id, email,
                      kind, action, quantity=1, note='', organiser=False):
        operation_id = str(operation_id or '').strip()
        if not 8 <= len(operation_id) <= 64 or not operation_id.isascii() or not operation_id.isalnum():
            raise WorkflowError('A valid unique operation ID is required.')
        if kind not in ('pizza', 'drink'):
            raise WorkflowError('Choose pizza or drink.')
        if action not in ('issue', 'redeem', 'void_issue', 'undo_redemption'):
            raise WorkflowError('Choose a supported ticket action.')
        if isinstance(quantity, bool):
            raise WorkflowError('Ticket quantity must be between 1 and 10.')
        try:
            count = int(quantity)
        except (ValueError, TypeError):
            raise WorkflowError('Ticket quantity must be between 1 and 10.') from None
        if str(quantity).strip() != str(count) or not 1 <= count <= 10:
            raise WorkflowError('Ticket quantity must be between 1 and 10.')
        note = str(note or '').strip()
        if len(note) > 500 or (action in ('void_issue', 'undo_redemption') and not note):
            raise WorkflowError('Corrections require a reason of at most 500 characters.')
        with self.sessions.begin() as session:
            self._staff(session, actor, organiser)
            existing = session.get(FoodDrinkTransaction, operation_id)
            if existing:
                if (existing.registration_id != str(registration_id or '') or
                    existing.member_email != str(email or '').strip().casefold() or
                    existing.kind != kind or existing.action != action or
                    existing.quantity != count):
                    raise WorkflowError('Operation ID is already used for another ticket transaction.')
                return False
            reg = self._team(session, registration_id)
            email = self._email(reg, email)
            if action in ('issue', 'void_issue', 'undo_redemption'):
                if not organiser and EventRoster._assigned_id(session, reg) != int(actor):
                    raise WorkflowError('Only the assigned monitor can issue or correct tickets.')
            balance = session.get(FoodDrinkBalance, (reg.id, email, kind))
            if balance is None:
                balance = FoodDrinkBalance(registration_id=reg.id,
                                           member_email=email, kind=kind,
                                           issued=0, redeemed=0)
                session.add(balance)
            if action == 'issue':
                balance.issued += count
            elif action == 'redeem':
                if balance.issued - balance.redeemed < count:
                    raise WorkflowError('No unredeemed ticket is available for this participant.')
                balance.redeemed += count
            elif action == 'void_issue':
                if balance.issued - balance.redeemed < count:
                    raise WorkflowError('Cannot void tickets that were already redeemed.')
                balance.issued -= count
            else:
                if balance.redeemed < count:
                    raise WorkflowError('There is no redemption to reverse.')
                balance.redeemed -= count
            session.add(FoodDrinkTransaction(
                operation_id=operation_id, registration_id=reg.id,
                member_email=email, kind=kind, action=action,
                quantity=count, actor_id=int(actor), note=note))
            session.add(Audit(
                team_id=reg.team_id or 0, batch_id='', actor_id=int(actor),
                action='food_drink_' + action, details={
                    'operation_id': operation_id, 'registration_id': reg.id,
                    'email': email, 'kind': kind, 'quantity': count,
                    'issued': balance.issued, 'redeemed': balance.redeemed,
                    'note': note,
                }))
            return True
