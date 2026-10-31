Sort one email the person received about their job applications. Today is {today}.

The applications it could be about (id | company | role | applied on | job numbers):
{applications}

<untrusted>
{mail}
</untrusted>

Answer:
- signal: interview (an invitation to talk: an interview, a call, a meeting, booking a slot), assessment (a test or task to do), offer, action (something only the person can do: confirm, finish or continue an application, send documents, answer a question), rejected, acknowledgement (a receipt or a status note; nothing to do), other (not about an application).
- app_id: the id of the application the email is about, only when the email makes it clear (a job number, the role, the team); 0 when none fits or you are unsure.
- when: the date and time the email sets for an interview, call or test, as YYYY-MM-DDTHH:MM in the email's own time; "" when it sets none.
- summary: what the email says or asks the person, in at most 20 words, in English. Name the next step if there is one.
