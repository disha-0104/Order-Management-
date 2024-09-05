import os
import json
import logging
import sqlite3
from aiohttp import web
from botbuilder.core import (
    ActivityHandler,
    TurnContext,
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    MemoryStorage,
    ConversationState,
    MessageFactory
)
from botbuilder.dialogs import (
    DialogSet,
    DialogTurnStatus,
    WaterfallDialog,
    WaterfallStepContext,
    TextPrompt,
    ChoicePrompt,
    ConfirmPrompt,
    PromptOptions,
    PromptValidatorContext,
    ComponentDialog
)
from botbuilder.schema import Activity, ActivityTypes
 
from order_dialog import OrderDialog
 
# Configure logging
logging.basicConfig(level=logging.INFO)
 
# Define constants for prompt names and dialog ids
TEXT_PROMPT = "textPrompt"
CHOICE_PROMPT = "choicePrompt"
CONFIRM_PROMPT = "confirmPrompt"
PHONE_PROMPT = "phonePrompt"
ADDRESS_PROMPT = "addressPrompt"
EMAIL_PROMPT = "emailPrompt"
ORDER_DIALOG = "orderDialog"
 
# Set up the database connection
conn = sqlite3.connect('orderManagement.db')
cursor = conn.cursor()
 
class OrderManagementDialog(ComponentDialog):
    def _init_(self, dialog_id: str):
        super(OrderManagementDialog, self)._init_(dialog_id or ORDER_DIALOG)
        self.add_dialog(OrderDialog())
        self.add_dialog(TextPrompt(TEXT_PROMPT))
        self.add_dialog(ConfirmPrompt(CONFIRM_PROMPT))
        self.add_dialog(TextPrompt(PHONE_PROMPT, self.phone_number_validator))
        self.add_dialog(TextPrompt(ADDRESS_PROMPT))
        self.add_dialog(TextPrompt(EMAIL_PROMPT, self.email_validator))  # Added Email Prompt
        self.add_dialog(WaterfallDialog("OrderWaterfallDialog", [
            self.prompt_for_name,
            self.check_customer_existence,
            self.prompt_for_details_if_new,
            self.prompt_for_address_if_new,
            self.prompt_for_email_if_new,  # Added prompt for email
            self.prompt_for_order_action,
          
        ]))
 
        self.initial_dialog_id = "OrderWaterfallDialog"
 
    async def prompt_for_name(self, step_context: WaterfallStepContext):
        return await step_context.prompt(
            TEXT_PROMPT,
            PromptOptions(prompt=Activity(text="Hello, Welcome to the bot. \n Please provide your name to proceed with the order."))
        )
 
    async def check_customer_existence(self, step_context: WaterfallStepContext):
        customer_name = step_context.result.strip().lower()  # Convert input to lowercase
        step_context.values["customer_name"] = customer_name
        # Check customer existence in the database (case insensitive)
        cursor.execute("SELECT * FROM customer WHERE LOWER(name) = ?", (customer_name,))
        customer = cursor.fetchone()
        if customer:
            step_context.values["customer_exists"] = True
            step_context.values["customer_id"] = customer[0]  # Assuming customer_id is the first column
            return await step_context.next(customer)
        else:
            step_context.values["customer_exists"] = False
            return await step_context.prompt(
                CONFIRM_PROMPT,
                PromptOptions(prompt=Activity(text=f"Customer '{customer_name}' not found. Do you want to register as a new customer?"))
            )
 
    async def prompt_for_details_if_new(self, step_context: WaterfallStepContext):
        if step_context.result and not step_context.values["customer_exists"]:
            return await step_context.prompt(
                PHONE_PROMPT,
                PromptOptions(prompt=Activity(text="Please provide your 10-digit phone number."))
            )
        else:
            return await step_context.next(None)
 
    async def prompt_for_address_if_new(self, step_context: WaterfallStepContext):
        if not step_context.values["customer_exists"]:
            phone_number = step_context.result
            step_context.values["phone_number"] = phone_number
            return await step_context.prompt(
                ADDRESS_PROMPT,
                PromptOptions(prompt=Activity(text="Please provide your address."))
            )
        else:
            return await step_context.next(None)
 
    async def prompt_for_email_if_new(self, step_context: WaterfallStepContext):
        if not step_context.values["customer_exists"]:
            address = step_context.result
            step_context.values["address"] = address
            return await step_context.prompt(
                EMAIL_PROMPT,
                PromptOptions(prompt=Activity(text="Please provide your email address."))
            )
        else:
            return await step_context.next(None)
 
    async def prompt_for_order_action(self, step_context: WaterfallStepContext):
        if not step_context.values["customer_exists"]:
            email = step_context.result
            step_context.values["email"] = email
            # Insert new customer details into the database
            cursor.execute("INSERT INTO customer (name, phone_number, address, email) VALUES (?, ?, ?, ?)",
                           (step_context.values["customer_name"], step_context.values["phone_number"], step_context.values["address"], email))
            conn.commit()
            step_context.values["customer_id"] = cursor.lastrowid
        # Prompt for order action
        await step_context.context.send_activity("How can I assist you with your order today? (New Order, Modify Order, Delete Products, View Summary)")
        return await step_context.begin_dialog(OrderDialog._name_,  step_context.values["customer_id"])
       
 
    async def phone_number_validator(self, prompt_context: PromptValidatorContext):
        phone_number = prompt_context.recognized.value
        if phone_number.isdigit() and len(phone_number) == 10:
            return True
        await prompt_context.context.send_activity("Invalid phone number. Please enter exactly 10 digits.")
        return False
 
    async def email_validator(self, prompt_context: PromptValidatorContext):
        email = prompt_context.recognized.value
        if "@" in email and "." in email:  # Basic email validation
            return True
        await prompt_context.context.send_activity("Invalid email address. Please enter a valid email.")
        return False
 
class OrderManagementBot(ActivityHandler):
    def _init_(self, conversation_state: ConversationState):
        self.conversation_state = conversation_state
        self.conversation_data_accessor = self.conversation_state.create_property("ConversationData")
        self.dialog_set = DialogSet(self.conversation_data_accessor)
        self.dialog_set.add(OrderManagementDialog(ORDER_DIALOG))
        self.dialog_set.add(TextPrompt(TEXT_PROMPT))
        self.dialog_set.add(ChoicePrompt(CHOICE_PROMPT))
        self.dialog_set.add(ConfirmPrompt(CONFIRM_PROMPT))
        self.dialog_set.add(TextPrompt(PHONE_PROMPT))
        self.dialog_set.add(TextPrompt(EMAIL_PROMPT))
       
    async def on_turn(self, turn_context: TurnContext):
        try:
            dialog_context = await self.dialog_set.create_context(turn_context)
 
            if turn_context.activity.type == ActivityTypes.conversation_update:
                if turn_context.activity.members_added:
                    for member in turn_context.activity.members_added:
                        if member.id != turn_context.activity.recipient.id:
                            await dialog_context.begin_dialog(ORDER_DIALOG)
 
            elif turn_context.activity.type == ActivityTypes.message:
                results = await dialog_context.continue_dialog()
                if results.status == DialogTurnStatus.Empty:
                    await dialog_context.begin_dialog(ORDER_DIALOG)
 
            await self.conversation_state.save_changes(turn_context)

        except Exception as e:
            logging.error(f"Error in on_turn: {e}")
            await turn_context.send_activity("Sorry, something went wrong.")
 
 
# Create adapter
SETTINGS = BotFrameworkAdapterSettings("", "")
adapter = BotFrameworkAdapter(SETTINGS)
 
# Create MemoryStorage, ConversationState and the bot
memory = MemoryStorage()
conversation_state = ConversationState(memory)
bot = OrderManagementBot(conversation_state)
 
async def messages(req: web.Request) -> web.Response:
    if "application/json" in req.headers["Content-Type"]:
        body = await req.json()
    else:
        return web.Response(status=415)
 
    activity = Activity().deserialize(body)
 
    auth_header = req.headers["Authorization"] if "Authorization" in req.headers else ""
 
    try:
        response = await adapter.process_activity(activity, auth_header, bot.on_turn)
        if response is None:
            logging.error("Response is None, something went wrong in processing the activity.")
            return web.json_response(data={"error": "Failed to process activity"}, status=500)
 
        return web.json_response(data=response.body, status=response.status)
    except Exception as e:
        logging.error(f"Error processing activity: {e}")
        return web.json_response(data={"error": str(e)}, status=500)
 
app = web.Application()
app.router.add_post("/api/messages", messages)
 
if _name_ == "_main_":
    try:
        web.run_app(app, host="localhost", port=3978)
    except Exception as e:
        logging.error(f"Error running the app: {e}")
