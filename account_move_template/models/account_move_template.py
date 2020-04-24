# Copyright 2015-2019 See manifest
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_round
from odoo.tools.safe_eval import safe_eval


class AccountMoveTemplate(models.Model):
    _name = "account.move.template"
    _description = "Journal Entry Template"

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        ondelete="cascade",
        default=lambda self: self.env["res.company"]._company_default_get(),
    )
    journal_id = fields.Many2one("account.journal", string="Journal", required=True)
    ref = fields.Char(string="Reference", copy=False)
    line_ids = fields.One2many(
        "account.move.template.line", inverse_name="template_id", string="Lines"
    )

    _sql_constraints = [
        (
            "name_company_unique",
            "unique(name, company_id)",
            "This name is already used by another template!",
        )
    ]

    @api.multi
    @api.returns("self", lambda value: value.id)
    def copy(self, default=None):
        self.ensure_one()
        default = dict(default or {}, name=_("%s (copy)") % self.name)
        return super(AccountMoveTemplate, self).copy(default)

    def eval_computed_line(self, line, sequence2amount):
        safe_eval_dict = {}
        for seq, amount in sequence2amount.items():
            safe_eval_dict["L%d" % seq] = amount
        try:
            val = safe_eval(line.python_code, safe_eval_dict)
            sequence2amount[line.sequence] = val
        except ValueError:
            raise UserError(
                _(
                    "Impossible to compute the formula of line with sequence %s "
                    "(formula: %s). Check that the lines used in the formula "
                    "really exists and have a lower sequence than the current "
                    "line."
                )
                % (line.sequence, line.python_code)
            )
        except SyntaxError:
            raise UserError(
                _(
                    "Impossible to compute the formula of line with sequence %s "
                    "(formula: %s): the syntax of the formula is wrong."
                )
                % (line.sequence, line.python_code)
            )

    def compute_lines(self, sequence2amount):
        prec = self.company_id.currency_id.rounding
        input_sequence2amount = sequence2amount.copy()
        for line in self.line_ids.filtered(lambda x: x.type == "input"):
            if line.sequence not in sequence2amount:
                raise UserError(
                    _(
                        "You deleted a line in the wizard. This is not allowed: "
                        "you should either update the template or modify the "
                        "journal entry that will be generated by this wizard."
                    )
                )
            input_sequence2amount.pop(line.sequence)
        if input_sequence2amount:
            raise UserError(
                _(
                    "You added a line in the wizard. This is not allowed: "
                    "you should either update the template or modify "
                    "the journal entry that will be generated by this wizard."
                )
            )
        for line in self.line_ids.filtered(lambda x: x.type == "computed"):
            self.eval_computed_line(line, sequence2amount)
            sequence2amount[line.sequence] = float_round(
                sequence2amount[line.sequence], precision_rounding=prec
            )
        return sequence2amount

    def generate_journal_entry(self):
        """Called by the button on the form view"""
        self.ensure_one()
        wiz = self.env["account.move.template.run"].create({"template_id": self.id})
        action = wiz.load_lines()
        return action


class AccountMoveTemplateLine(models.Model):
    _name = "account.move.template.line"
    _description = "Journal Item Template"
    _order = "sequence, id"

    template_id = fields.Many2one(
        "account.move.template", string="Move Template", ondelete="cascade"
    )
    name = fields.Char(string="Label", required=True)
    sequence = fields.Integer("Sequence", required=True)
    account_id = fields.Many2one(
        "account.account",
        string="Account",
        required=True,
        domain=[("deprecated", "=", False)],
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        domain=["|", ("parent_id", "=", False), ("is_company", "=", True)],
    )
    analytic_account_id = fields.Many2one(
        "account.analytic.account", string="Analytic Account"
    )
    analytic_tag_ids = fields.Many2many("account.analytic.tag", string="Analytic Tags")
    tax_ids = fields.Many2many("account.tax", string="Taxes")
    tax_line_id = fields.Many2one(
        "account.tax", string="Originator Tax", ondelete="restrict"
    )
    company_id = fields.Many2one(related="template_id.company_id", store=True)
    company_currency_id = fields.Many2one(
        related="template_id.company_id.currency_id",
        string="Company Currency",
        store=True,
    )
    note = fields.Char()
    type = fields.Selection(
        [("computed", "Computed"), ("input", "User input"),],
        string="Type",
        required=True,
        default="input",
    )
    python_code = fields.Text("Python Code")
    move_line_type = fields.Selection(
        [("cr", "Credit"), ("dr", "Debit")], required=True, string="Direction"
    )
    payment_term_id = fields.Many2one(
        "account.payment.term",
        string="Payment Terms",
        help="Used to compute the due date of the journal item.",
    )

    _sql_constraints = [
        (
            "sequence_template_uniq",
            "unique(template_id, sequence)",
            "The sequence of the line must be unique per template!",
        )
    ]

    @api.constrains("type", "python_code")
    def check_python_code(self):
        for line in self:
            if line.type == "computed" and not line.python_code:
                raise ValidationError(
                    _("Python Code must be set for computed line with " "sequence %d.")
                    % line.sequence
                )
