#!venv/bin/python
# -*- coding: utf-8 -*-

import os
import time
import csv
import sys
import copy
import pytz
import json
import re
from functools import wraps
from operator import itemgetter
from datetime import datetime, timedelta
import locale
locale.setlocale(locale.LC_ALL, '')
from flask import Flask, url_for, redirect, render_template, request, abort, flash, send_from_directory, Markup, jsonify, session
from flask_mongoengine import MongoEngine
from flask_mongoengine.wtf import model_form
from flask_security import Security, \
    UserMixin, RoleMixin, login_required, current_user, datastore, MongoEngineUserDatastore
from flask_security.utils import encrypt_password
import hashlib
import base64
from flask_mail import Mail
import flask_admin
from flask_session import Session
from flask_admin import helpers as admin_helpers
from flask_admin import BaseView, expose, AdminIndexView
from flask_bootstrap import Bootstrap
from flask_datepicker import datepicker
from flask_admin.contrib.pymongo import ModelView
from flask_wtf import Form, FlaskForm
from wtforms import StringField, PasswordField, TextField, SubmitField, validators, HiddenField, FloatField, DecimalField, IntegerField, TextAreaField, SelectField, FloatField
from flask_wtf.file import FileField, FileRequired
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
from werkzeug.datastructures import CombinedMultiDict
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
from bson import json_util
from flask.json import JSONEncoder
import random
from flask_login import LoginManager
from fba_locations import fba_locations
from mongoengine.queryset.base import BaseQuerySet

import stripe

import xlrd
## We're developing on Python 2 and 3
env_version = sys.version_info


# Create Fldask application
app = Flask(__name__)
app.config.from_pyfile('config.py')
sess = Session()
sess.init_app(app)
csrf = CSRFProtect()
csrf.init_app(app)
Bootstrap(app)
datepicker(app)

mongo = PyMongo(app)
enginedb = MongoEngine(app)
mail = Mail(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
#Session(app)
#scheduler = BackgroundScheduler()
#atexit.register(lambda: scheduler.shutdown())
now = datetime.now()
nice_now = datetime.strftime(now,"%m-%d-%Y at %I:%M %p")

today = datetime(now.year, now.month, now.day, 23,59,59)

stripe.api_key = app.config['STRIPE_PRIV']

##########
### PLACEHOLDER VARIABLES FOR USER SETTINGS
########

default_range = 29
#barcode_type = 'UPC'
lead_time = 30
inventory_target = 90 #Buffer amount
#restock_buffer = 30 #Send restock alerts when this # of days reached
fba_target = 45
fba_resend = 15
fba_minimum = 6
results = {}

#restock_alert_frequency= ['Never','Daily','Weekly','Monthly']
#timezone = 'EDT'
#calculate_discount = True
#ignore_weekend_shipping_speed = True
#ignore_qty_sent_to_amz = True

######################################
############# FUNCTIONS ##############
######################################
def StripMicroseconds(datestring) :
    output = str(datestring).split('.')
    output = output[0]
    return output

def DateFormHandler(formvalue):
    ### User Defaults for Date Handling applied here
    if not formvalue :
        start = today - timedelta(days=default_range, hours=23, minutes=59, seconds=59)
        end = today
    elif type(formvalue) == int :
        start = today - timedelta(days=formvalue, hours=23, minutes=59, seconds=59)
        end = today

    else :
        daterange = formvalue.split(' - ')
        start = datetime.strptime(daterange[0]+" 00:00:00",'%m/%d/%Y %H:%M:%S')
        end = datetime.strptime(daterange[1]+" 23:59:59", '%m/%d/%Y %H:%M:%S')
        #timezone = pytz.timezone("America/Los_Angeles")
        #start = timezone.localize(start)
        #end = timezone.localize(end)

    return start, end

def StripTimezone(datestring) :
    output = str(datestring).split('.')
    output = output[0]
    return output

def AddressEndingStrip (address) :
    strip_list = [
    'RD','ROAD','RD.',
    'BLVD.','BLVD','BOULEVARD',
    'DRIVE','DR','DR.',
    'CIRCLE','CIR','CIR.',
    'SUITE','STE','STE.','#'
    ]
    address = address.split(" ")

    return list(set(address) - set(strip_list))

def CheckUserFeatures(sb_auth):
    @wraps(sb_auth)
    def decorated_function(*args, **kwargs):
        ###################################################
        ### Redirect Users Who Have Not Finished Setup ###
        ###################################################
        user_details = mongo.db.mongo_user.find_one({'email':session['email']})

        #user_features = results['user_features']
        #flash(user_features)
        if 'ss_key' and 'ss_secret' not in user_details :
            return redirect(url_for('settings.SetupView'))

        if 'alias_updated' not in user_details:
            return redirect(url_for('settings.StepTwoView'))
            #return

        if 'inventory_updated' not in user_details:
            return redirect('settings.StepThreeView')

        return sb_auth(*args, **kwargs)
    return decorated_function

######
### GLOBALS
####

class StockBoy() :

    def __init__(self):
        email = current_user.email #'lazyluckyfree@gmail.com'#
        ### Development Email Address
        #session['email'] = 'matt@charlieandwobbs.com'
        session['email']=email
        #self.results = {}
        ### User Feature & Setup Check ###
        #flash(user_details)

        self.results={}
        #flash(user_features)

        ### Logic for Billing, Trial & Cancellation Goes here
        #session['cached'] = True
        #flash('Stockboy Initiated')

        if self.IsExpired('init_timeout') :
            session['top_bar'] = {}
            self.StoreDictBuilder()
            self.ShipperDictBuilder()
            self.ProductDictBuilder()
            self.SupplierDictBuilder()
            self.AMZLocationBuilder()

            #flash('Stockboy init dicts built')
            session['init_timeout'] = now


        if self.IsExpired('alias_timeout'):
            self.AliasDictBuilder()
            session['alias_timeout'] = now


        if self.IsExpired('inventory_timeout'):
            self.SSInventoryBuilder()
            session['inventory_timeout'] = now

        if self.IsExpired('fba_timeout'):
            self.FBADictBuilder()
            session['fba_timeout'] = now


    #############################
    ######## UTILITIES ##########
    #############################

    def DateFirstSale(self, sku) :
        pass

    def SKUFlatten (self, sku):

        alias_dict = session['alias_dict']
        if sku :
            sku = sku.strip()

        if sku in alias_dict:
            sku = alias_dict[sku]

        if not sku :
            pass
            #sku = 'sb-'+str(random.randint(123456,234567))

        return sku

    def AMZCheck(self, street1, state) :

        amz_address = session['address_list']

        ship_add = street1.upper()
        ship_add = AddressEndingStrip(ship_add)


        if ship_add in amz_address :
            amz_transfer = True
        else :
            amz_transfer = False

        return amz_transfer

    #### To build
        def TableBuilder():
            pass

        def DiscountHandler():

            #if "Discount" in name :
                #continue

            ## Discount Handler ##

            #discount = 0
            #discount_percent = False

            #for item in l['items']:
            #    iter_sku = item['sku']
            #    if 'Discount' in item['name'] :
            #        discount = item['unitPrice']

            #    if iter_sku not in alias_list :
            #        continue
            #if discount != 0 :
            #    if discount < 0:
            #        discount = discount * -1
            #    gross = discount+order_total
            #    discount_percent = discount/gross

            #    item_discount = sales_total*discount_percent`
            pass

    def DateFormController(self) :
        if 'dateformvalue' in session :
            start, end = DateFormHandler(session['dateformvalue'])
        else :
            start, end = DateFormHandler(default_range)

        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        processed = False

        if 'start_date' and 'end_date' in session:
            if start_date == session['start_date']:
                if end_date == session['end_date']:
                    processed = True

        if not processed or 'date_dict' not in session:

            session['start'] = start
            session['end'] = end
            session['start_date'] = start_date
            session['end_date'] = end_date
            session['delta_range'] = (end - start).days+1
            session['orders_timeout'] = now

            ## Run the date builder
            self.DateDictBuilder(start, end)


        session['processed'] = processed

    def ExpireCache(self, timer):
        ## Simple helper to set the timeout
        session[timer] = now - timedelta(days=100)

    def IsExpired(self, timer) :
        ## If you wanted to adjust cache duration,
        ## you would do it here
        if session.get(timer) :
            if now - session[timer] < timedelta(hours=1):
                expired = False
                #flash(timer+ ' Checked, Still Cached')
            else :
                #flash(timer+' Checked, Expired')
                expired = True

        else :
            #flash(timer+' Checked, Does not exist')
            expired = True

        return expired

    def AMZLocationBuilder(self):
        address_list = []
        for location in fba_locations.values() :
            address=location['address'].upper()
            address = AddressEndingStrip(address)
            #zip = location['zip']
            state = location['state']
            address_list.append(address)

        session['address_list'] = address_list

    def OrderCacheAndCursor(self):
        ##########################
        # Typical Report Action is to cycle order
        # Check the Order Timeout Cache & Process'd
        # Determine if report needs to be rebuilt


        if self.IsExpired('orders_timeout') or not session['processed']:
            #flash('Session cached')
            cursor = mongo.db.orders.find(
            {'$and':[
                {'orderDate':
                    {'$lte': session['end'],
                    '$gte':session['start']}
                },
                {'owner':session['email']}
            ]}
            )

            self.ReportDictBuilder(cursor)

            #flash('Orders Built')

        else:
            pass
            #flash ('Orders Cached')

    ##############################
    ###### INIT BUILDERS #########
    #############################
    def StoreDictBuilder(self):
        store_dict = {}

        cursor = mongo.db.stores.find({
        'owner':session['email']
        })

        for store in cursor :
            store['_id']=str(store['_id']) # This fixes session storing issue
            store_id = store['storeId']
            store_dict[store_id] = store

        session['store_dict'] = store_dict

    def ShipperDictBuilder(self) :
        shipper_dict = {}

        cursor = mongo.db.users.find()

        for shipper in cursor :
            shipper_dict[shipper['userId']] = {}
            sd = shipper_dict[shipper['userId']]
            sd['name']=shipper['name']
            sd['owner']=shipper['owner']
            sd['username']=shipper['userName']

        session['shipper_dict'] = shipper_dict

    def AliasDictBuilder(self):
        ## Alias Dictionary can be built based on context
        ## For example, a single sku or all Owner Alias, etc
        session['alias_timeout'] = now

        alias_search = mongo.db.alias.find({'Owner':session['email']})

        alias_dict = {}
        for alias in alias_search :
            alias_dict[alias['Alias']] = alias['Product SKU'].strip()

        #self.results['alias_dict'] = alias_dict
        session['alias_dict'] = alias_dict

        if len(alias_dict) == 0 :
            flash(Markup('Please Upload Your Product <a href="https://stockboy.co/dashboard/import/#alias-card">Alias List from Shipstation</a>.'))

    ########################################
    ###### PRIMARY REPORT BUILDERS #########
    ########################################

    def ReportDictBuilder(self, order_cursor):

        ### Define a number of outputs that come from iterating through orders
        ### Create all possible dictionaries / outputs at once with a single loop of orders
        ### Cache this and encourage user to set a default range to maximize speed to delivery
        session['orders_timeout'] = now

        if 'target_sku' in self.results.keys() :
            target_sku = self.results['target_sku']
            ## Don't cache individual items

            self.ExpireCache('orders_timeout')

        else :
            target_sku = 'All'


        #### OUTPUT ########
        ### TOTAL SALES DICTS ######
        category_sales_dict = {}
        store_sales_dict = {}
        sku_sales_dict = {}
        customer_sales_dict = {}
        ordered_together_dict = {}
        orders_dict = {}
        sales_rank= {}
        qty_rank = {}

        #channel_sales_dict = {}

        ##### BY DAY DICTS ######
        by_day_dict = copy.deepcopy(session['date_dict']) ## This does not need to be passed to the session
        #channel_sales_dict = copy.deepcopy(self.results['date_dict'])

        for year in by_day_dict.values() :
            for month in year.values() :
                for day in month.keys():
                    month[day] = {
                        'meta':{
                        'sales':0,
                        'cogs':0,
                        'gross':0,
                        'qty':0,
                        'fba':0,
                        'orders': 0,
                        'discounts':0,
                        'shipping':0},
                        'channel':{}
                        }

        ## Top Bar / Top level metrics
        total_qty = 0
        total_sales = 0
        shipped_to_amz = 0
        total_discounts = 0
        total_shipping = 0
        total_cogs = 0
        total_gross = 0
        total_orders = 0
        total_cancelled = 0

        total_inventory = session['inventory_dict']['meta']['total_stock'] + session['fba_dict']['meta']['total_stock']
        total_stock_value = session['inventory_dict']['meta']['stock_value'] + session['fba_dict']['meta']['stock_value']
        ### Supporting Variables
        #alias_dict
        ##product_dict
        marketplaces_with_sales = []

        delta_range = session['delta_range']
        store_dict = session['store_dict']
        sku_category_dict = session['sku_category_dict']

        inventory_dict = session['inventory_dict']
        fba_dict = session['fba_dict']

        repeat_customers = []

        ## Sku
        #### Name
        #### imageUrl
        #### quantity_sold
        #### unit price
        #### 'stores' [store_id] ($,qty)
        #### Inventory Available
        #### Product Category
        ####

        for order in order_cursor :
            ## Just the raw orders in a dict
            orders_dict[order['orderId']] = order
            ## Perhaps the above is a better way to do this
            store_id = order['advancedOptions']['storeId']
            customer_id = order['customerId']

            warehouse_id = order['advancedOptions']['warehouseId']
            create_date = order['createDate']
            timezone = pytz.timezone("America/Los_Angeles")

            order_date = order['orderDate']
            order_date = timezone.localize(order_date)
            order_id = order['orderId']
            order_number = order['orderNumber']
            order_status = order['orderStatus']

            if order_status == 'cancelled':
                total_cancelled += 1
                continue

            order_total = order['orderTotal']
            order_qty = 0
            order_cogs = 0
            order_gross = 0

            shipping_amt = order['shippingAmount']
            street1 = order['shipTo']['street1']
            customer_name = order['shipTo']['name']
            city = order['shipTo']['city']
            state = order['shipTo']['state']
            zip = order['shipTo']['postalCode']
            email = order['customerEmail']
            calculated_total = 0
            discounts = 0
            ordered_together = []
            marketplace = store_dict[store_id]['marketplaceName']
            if marketplace not in marketplaces_with_sales:
                marketplaces_with_sales.append(marketplace)
            is_amz = False
            amz_transfer = self.AMZCheck(street1, state)
            order['amz_transfer'] = amz_transfer
            if marketplace == 'Amazon':
                shipping_amt = 0
                is_amz = True

            ## Shorthand for by day dict for this order date
            bdd_thisday = by_day_dict[order_date.year][order_date.month][order_date.day]

            for item in order['items'] :

                if item['sku'] == '':
                    item_sku = item['orderItemId']
                else :
                    item_sku = self.SKUFlatten(item['sku'])

                item['flat_sku'] = item_sku
                ordered_together.append(item_sku)

                if target_sku != 'All' and target_sku != item_sku:
                    ## Ineffecient method of searching for orders matching target sku
                    ## Likely a more clever mongodb call should handle the product sku page
                    continue

                price = item['unitPrice']
                qty = item['quantity']

                img = item['imageUrl']
                name = item['name']

                if amz_transfer :
                    bdd_thisday['meta']['fba']+= qty
                    shipped_to_amz+= qty

                    continue

                if item_sku in inventory_dict :
                    if 'SB Cost' in inventory_dict[item_sku]:
                        cost = inventory_dict[item_sku]['SB Cost']
                    else :
                        cost = 0
                else :
                    cost = 0
                cogs = cost*qty
                gross = price - cogs
                order_cogs += cogs
                order_gross += gross
                ## Make all discounts a negative number
                if price < 0 or 'discount' in name.lower() :
                    if price > 0 :
                        price = price * -1

                    discounts += price
                    calculated_total += price
                    qty = 0 #Do not count discount as an item
                else :
                    calculated_total += qty*price

                if item_sku in sku_category_dict :
                    category_id = sku_category_dict[item_sku][0]
                    category_name = sku_category_dict[item_sku][1]

                else:
                    category_id = None
                    category_name = 'None'


                ## Amazon orders have exited, add to total qty count which gets added to topbar total qty
                order_qty += qty
                ## Here's where we should look into issues topline total


                ### Items not transfered to Amazon get added to By Day Dict
                if item_sku in bdd_thisday :
                    bdd_thisday[item_sku]['sales']+= price*qty
                    bdd_thisday[item_sku]['qty']+= qty
                    bdd_thisday[item_sku]['cogs']+= cogs
                    bdd_thisday[item_sku]['gross']+= gross
                    ## Historic Stock Value Goes here
                else :
                    bdd_thisday[item_sku] = {
                        'sales': price*qty,
                        'qty': qty,
                        'cogs': cogs,
                        'gross': gross
                    }

                ### Items not transfered to Amazon get counted in existing record
                if item_sku in sku_sales_dict :
                    ssd = sku_sales_dict[item_sku]
                    ssd['sku'] = item_sku
                    ssd['sales'] += price*qty ## Discounts should be handled here?
                    ssd['qty']+= qty
                    ssd['burn'] = float(ssd['qty'])/float(delta_range)
                    ssd['cogs'] += cogs
                    ssd['gross']+= gross
                    ssd['daily_cost'] = float(ssd['cogs'])/float(delta_range)
                    ssd['order_list'].append(order_id)

                    sales_rank[item_sku] += price*qty
                    qty_rank[item_sku] += qty

                    if item_sku in inventory_dict :
                        ssd['stock'] = inventory_dict[item_sku]['Available']

                    if is_amz :

                        ssd['amz-sales'] += price*qty
                        ssd['amz-qty'] += qty
                        ssd['amz-cogs']+= cogs
                        ssd['amz-gross']+= gross
                        ssd['asin'] = item['sku']
                        if item_sku in fba_dict:
                            ssd['fba_stock'] = fba_dict[item_sku]['InStockSupplyQty']


                    if not ssd['img']:
                        ssd['img'] = img

                    if marketplace in ssd['channel']:
                        ssd['channel'][marketplace]['sales'] += price*qty
                        ssd['channel'][marketplace]['qty'] += qty
                        ssd['channel'][marketplace]['cogs'] += cogs
                        ssd['channel'][marketplace]['gross'] += gross
                        #ssd['channel'][marketplace]['orders'] += 1

                    else :
                        ssd['channel'][marketplace] = {
                        'sales':price*qty,
                        'qty':qty,
                        'cogs': cogs,
                        #'orders': 1,
                        'gross': gross,

                        }


                    #if store_dict[store_id]['marketplaceName'] in
                    #ssd['channel'][]



                ### Else Create a new record
                else :
                    if is_amz:
                        amz_sales = price*qty
                        amz_qty = qty
                        amz_cogs = cogs
                        amz_gross = gross

                    else :
                        amz_sales = 0
                        amz_qty = 0
                        amz_cogs = 0
                        amz_gross = 0

                    sales_rank[item_sku]=price*qty
                    qty_rank[item_sku]=qty

                    sku_sales_dict[item_sku] = {
                    'sku': item_sku,
                    'name': name,
                    'category': category_name,
                    'img': img,
                    'sales': price*qty,
                    'qty': qty,
                    'cogs': cogs,
                    'gross': gross,
                    'burn': qty/delta_range,
                    'daily_cost': cogs/delta_range,
                    'amz-sales': amz_sales,
                    'amz-qty':amz_qty,
                    'amz-cogs': amz_cogs,
                    'amz-gross': amz_gross,
                    'order_list': [order_id],
                    'channel':{
                        marketplace:
                            {'sales':price*qty,
                            'qty':qty,
                            'cogs': cogs,
                            'gross': gross
                            }
                        }
                    }

                #### Category Sales Dict Input
                if category_id in category_sales_dict :
                    csd = category_sales_dict[category_id]
                    csd['sales'] += price*qty
                    csd['qty'] += qty

                else :
                    category_sales_dict[category_id] = {
                    'name' : category_name,
                    'sales': price*qty,
                    'qty': qty
                    }

            if len(ordered_together) > 1 :
                ordered_together_dict[order_id] = ordered_together


            ## STILL INSIDE ORDER, SET ORDER LEVEL ITEMS ##

            #### SALES & QTY BY DAY #####
            bdd_thisday['meta']['sales']+= calculated_total
            bdd_thisday['meta']['qty']+= int(order_qty)
            bdd_thisday['meta']['discounts']+= discounts
            bdd_thisday['meta']['cogs']+= order_cogs
            bdd_thisday['meta']['gross']+= order_gross
            bdd_thisday['meta']['orders']+= 1
            if marketplace in bdd_thisday['channel'].keys():
                bdd_thisday['channel'][marketplace]['sales'] += calculated_total
                bdd_thisday['channel'][marketplace]['qty'] += int(order_qty)
                bdd_thisday['channel'][marketplace]['discounts'] += discounts
                bdd_thisday['channel'][marketplace]['cogs'] += order_cogs
                bdd_thisday['channel'][marketplace]['gross'] += order_gross
                bdd_thisday['channel'][marketplace]['orders']+=1
            else :
                bdd_thisday['channel'][marketplace] = {
                'sales': calculated_total,
                'qty': int(order_qty),
                'discounts': discounts,
                'cogs':  order_cogs,
                'gross': order_gross,
                'orders':1,
                }

            ### Calculate Top Level Stats####
            total_qty += order_qty
            # All discounts are negative and were not part of item calculation because their quantity is zero
            total_sales += calculated_total
            total_discounts += discounts
            total_shipping += shipping_amt
            total_cogs += order_cogs
            total_gross += order_gross
            total_orders +=1

            order['orderQuantity'] = order_qty

            #### STORE / MARKETPLACE CHANNEL SALES ####
            ### Can add day dict nested for each marketplace
            if store_id in store_sales_dict :
                store_sales_dict[store_id]['sales']+= calculated_total
                store_sales_dict[store_id]['qty']+= order_qty
                store_sales_dict[store_id]['shipping'] += shipping_amt
                store_sales_dict[store_id]['discounts'] += discounts
            else :
                store_sales_dict[store_id] = {
                'name': store_dict[store_id]['storeName'],
                'sales': calculated_total,
                'qty': order_qty,
                'discounts': discounts,
                'shipping': shipping_amt
                }


            #### CUSTOMER SALES DETAILS ######
            if customer_id in customer_sales_dict :
                ## Customer ID coorilates with customer email
                customer_sales_dict[customer_id]['sales'] += calculated_total
                customer_sales_dict[customer_id]['qty' ]+= order_qty
                customer_sales_dict[customer_id]['discounts' ]+= discounts
                customer_sales_dict[customer_id]['shipping' ]+= shipping_amt
                customer_sales_dict[customer_id]['orders' ]+= 1

                if not customer_id in repeat_customers:
                    repeat_customers.append(customer_id)


            elif customer_id  :
                customer_sales_dict[customer_id]={
                'customer_id':customer_id,
                'name': order['billTo']['name'],
                'company': order['shipTo']['company'],
                'sales': calculated_total,
                'qty': order_qty,
                'orders': 1,
                'shipping': shipping_amt,
                'discounts': discounts,
                'street1': street1,
                'city': city,
                'state': state,
                'email': email
                }







        ########################
        #### ROLLUP RESULTS ####
        ########################

        # By ID results
        session['category_sales_dict'] = category_sales_dict
        session['store_sales_dict'] = store_sales_dict
        session['sku_sales_dict'] = sku_sales_dict
        session['customer_sales_dict'] = customer_sales_dict
        #session['sorted_sku_sales'] = sorted(sku_sales_dict.items(), key=lambda kv: kv['sales'])
        # By Day results
        #self.results['sales_by_day_dict'] = sales_by_day_dict
        #self.results['qty_by_day_dict'] = qty_by_day_dict
        #self.results['fba_qty_by_day_dict'] = fba_qty_by_day_dict
        session['by_day_dict'] = by_day_dict
        session['marketplaces_with_sales'] = marketplaces_with_sales

        session['ordered_together_dict'] = ordered_together_dict

        ## Orders
        session['orders_dict'] = orders_dict
        #### Calculate Average Burn Rate ####
        #### Take each burn rate and get an average ###
        ### Multiply by delta_range to get sold per sku per time period ###

        burn_rates = []
        for sku in sku_sales_dict :
            burn_rates.append(sku_sales_dict[sku]['burn'])

        if len(burn_rates) > 0:
            avg_burn = (sum(burn_rates)/len(burn_rates)) * session['delta_range']
            avg_burn = round(avg_burn,2)
        else :
            avg_burn = 0

        qty_per_day = total_qty/delta_range

        if total_orders > 0:
            items_per_order = total_qty/total_orders
            average_order_value = total_sales/total_orders
        else :
            items_per_order = 0
            average_order_value = 0

        ### Sort Sales & QTY Rank Dictionaries
        ### This converts them into a list of tupples
        ### We can iterate through these in order with Jinja to get rank.
        ### Pull SKU details, candidate for primary loop iter
        session['sales_rank'] = sorted(sales_rank.items(), key=lambda kv: kv[1], reverse=True)
        session['qty_rank'] = sorted(qty_rank.items(), key=lambda kv: kv[1], reverse=True)


        session['top_bar'] = {
            'total_qty': total_qty,
            'total_sales': total_sales,
            'total_cogs': total_cogs,
            'total_gross': total_gross,
            'total_cancelled': total_cancelled,
            'shipped_to_amz':shipped_to_amz,
            'avg_burn':  avg_burn,
            'avg_order_value': average_order_value,
            'items_per_order': items_per_order,
            'cost_per_day': total_cogs / delta_range,
            'qty_per_day': qty_per_day,
            'discounts': total_discounts,
            'shipping': total_shipping,
            'gross_sales': total_sales+total_shipping,
            'total_inventory': total_inventory,
            'total_stock_value': total_stock_value,
            'total_orders': total_orders,
            'total_skus_sold': len(sku_sales_dict.keys()),
            'total_categories_sold': len(category_sales_dict.keys()),
            'unique_customers': len(customer_sales_dict.keys()),
            'repeat_customers': len(repeat_customers)


            }

    def OrderedTogether(self, sku):
        ordered_together_dict = session['ordered_together_dict']

        ordered_with = {}
        for sku_list in ordered_together_dict.values() :
            if sku in sku_list :
                for product in sku_list :
                    if product != sku :
                        if product in ordered_with :
                            ordered_with[product]+=1
                        else:
                            ordered_with[product] = 1

        session['ordered_with'] = ordered_with

    def InventoryResultsBuilder(self):

        ssd = session['sku_sales_dict']
        fbad = session['fba_dict']
        pd = session['product_dict']

        total_sold = 0
        total_inventory = 0
        total_value = 0
        total_skus = 0

        ## Final Results go here
        inventory_results_dict = {}

        for item in session['inventory_dict'].values() :
            if 'SKU' not in item :
                continue
            sku = item['SKU']
            inventory_results_dict[sku] = {}
            row = inventory_results_dict[sku]
            row['SKU'] = sku
            row['name'] = pd[sku]['name']
            if sku in ssd :
                row['img'] = ssd[sku]['img']
            else :
                row['img'] = '#'
            stock = item['Stock']
            row['stock'] = stock
            if sku in fbad :
                if 'InStockSupplyQty' in fbad[sku]:
                    fba = fbad[sku]['InStockSupplyQty']
                else :
                    fba = fbad[sku]['TotalSupplyQty']
            else :
                fba = 0
            row['fba'] = fba
            ## Add Cost Logic (funciton call) Here
            if 'SB Cost' in item:
                row['cost'] = item['SB Cost']
            else:
                row['cost'] = item['Last Cost']

            if sku in ssd :
                sold = ssd[sku]['qty']
            else :
                sold = 0
            row['sold'] = sold

            if sold > 1 :
                burn = float(float(sold)/float(default_range))
                days = (stock+fba)/burn

            else :
                days = 9999
            row['days'] = days


            total_inventory += stock+fba
            total_sold += sold
            total_value += row['cost']*(stock+fba)
            total_skus += 1


        #### THIS NEEDS TO BE PAGE SPECIFIC
        #afd
        #top_bar = session['top_bar']

        ## Run some tests here to see if you get the same values

        session['top_bar']['page_total_inventory'] = total_inventory
        session['top_bar']['page_total_sold'] = total_sold
        session['top_bar']['page_total_value'] = total_value
        session['top_bar']['page_total_skus'] = total_skus

        total_qty = session['top_bar']['total_qty']

        if total_qty > 0:
            total_days = total_inventory/(total_qty/session['delta_range'])
        else :
            total_days = 0
            #flash('No inventory uploaded')

        session['top_bar']['total_days'] = total_days
        #session['top_bar'] = top_bar
        session['inventory_results_dict'] = inventory_results_dict


    ##############################
    #### DICTIONARY BUILDERS #####
    ##############################

    def DateDictBuilder(self, start,end):
        ## Build date range dictionary
        ## Documentation at: https://gist.github.com/jasonhdavis/73664baf24bdb595ffe0b66d01703c01
        date_dict={}
        labels = []
        date_dict.update({start.year:{start.month:{start.day: 0}}})
        delta_range = (end - start).days+1

        y_keys = [str(start.year)]
        m_keys = [str(start.month)+"-"+str(start.year)]

        for i in range(delta_range):
            iter_date = start+timedelta(days=i)
            year = iter_date.year
            month = iter_date.month
            day = iter_date.day
            labels.append(iter_date.strftime('%b %d'))
            if str(year) in y_keys and str(month)+"-"+str(year) in m_keys :
                date_dict[year][month][day] = 0
            elif str(year) in y_keys :
                date_dict[year].update({month:{day:0}})
                m_keys.append(str(month)+"-"+str(year))
            else :
                date_dict.update({year:{month:{day:0}}})
                y_keys.append(str(year))
                m_keys.append(str(month)+"-"+str(year))


        session['date_dict'] = date_dict
        session['date_range_labels'] = labels

    def ChartValueBuilder(date_dict):
        values=[]

        for year in date_dict.values() :
            for month in year.values() :
              for day in month.values() :
                  values.append(day)

        self.loading['values'] = values

    ## Inventory Dictionaries
    def ProductDictBuilder(self):

        #if not 'product_dict' in session['results']:

        product_dict = {}

        range_search = mongo.db.products.find({'owner':session['email']})

        #fba_inventory = mongo.db.mws.find({'owner':self.results['email']})

        #ss_inventory = mongo.db.inventory.find({'owner':self.results['email']})

        for product in range_search :
            sku = product['sku']
            product['_id'] = str(product['_id'])
            product_dict[sku]=product

        session['product_dict'] = product_dict

        #session['product_dict'] = product_dict

        sku_category_dict = {}


        #### AT this point, you should add in inventory values from import
        ### Determine if there is one product listing per alias
        ###

        for product in product_dict.values() :
            sku = product['sku']
            id = product['productId']
            name = product['name']
            length = product['length']
            width = product['width']
            height = product['height']
            weight = product['weightOz']
            #created = product['createDate']

            if product['productCategory'] :
                cat_id = product_dict[sku]['productCategory']['categoryId']
                cat_name = product_dict[sku]['productCategory']['name']
            else :
                cat_id = 9999
                cat_name = 'uncategorized'

            sku_category_dict[sku] = (cat_id, cat_name)

        session['sku_category_dict'] = sku_category_dict

    def SSInventoryBuilder(self) :
        session['inventory_timeout'] = now

        cursor = mongo.db.inventory.find({'Owner':session['email']})
        inventory_dict = {}

        #meta variables
        sku_count = 0
        total_stock = 0
        stock_value = 0
        for item in cursor :

            sku = item['SKU']
            sku = self.SKUFlatten(sku)
            inventory_dict[sku] = {}
            row = inventory_dict[sku]
            row['SKU'] = sku
            row['Stock'] = item['Stock']
            row['Last Cost'] = item['Last Cost']
            row['Avg Cost'] = item['Avg Cost']

            if 'SB Cost' in item :
                row['SB Cost'] = item['SB Cost']
                stock_value += item['Stock'] * item['SB Cost']
                row['Stock Value'] = item['Stock'] * item['SB Cost']
            elif row['Last Cost'] > row['Avg Cost'] :
                row['Stock Value'] = row['Last Cost'] * row['Stock']
            else :
                row['Stock Value'] = row['Avg Cost'] * row['Stock']

            row['Allocated'] = item['Allocated']
            row['Available'] = item['Available']


            sku_count += 1
            total_stock += item['Stock']


        inventory_dict['meta'] = {
        'sku_count': sku_count,
        'total_stock' : total_stock,
        'stock_value' : stock_value
        }

        session['inventory_dict'] = inventory_dict

    #Amz Address List Here
    def FBADictBuilder(self) :
        session['fba_timeout'] = now

        #build address list


        ## BUILD FBA INVENTORY FROM MWS COLLECTION
        cursor = mongo.db.mws.find({'Owner':session['email']})
        sku_count = 0
        total_stock = 0
        stock_value = 0
        fba_dict = {}
        for item in cursor :
            asin = item['ASIN']
            sku = self.SKUFlatten(asin)

            if sku not in fba_dict :
                fba_dict[sku] = {}
                fba_dict[sku]['ASIN'] = asin
                fba_dict[sku]['SellerSKU'] = item['SellerSKU']
                fba_dict[sku]['InStockSupplyQty'] = int(item['InStockSupplyQuantity'])
                fba_dict[sku]['Condition'] = item['Condition']

                sku_count += 1
                total_stock += int(item['InStockSupplyQuantity'])

                if 'Title' in item :
                    fba_dict[sku]['Title'] = item['Title']
                    fba_dict[sku]['ProductGroup'] = item['ProductGroup']
                    fba_dict[sku]['img'] = item['img']
                    fba_dict[sku]['Brand'] = item['Brand']
                    fba_dict[sku]['Rank'] = item['Rank']

                if sku in session['inventory_dict'] :
                    if 'SB Cost' in session['inventory_dict'][sku]:
                        stock_value += int(item['InStockSupplyQuantity']) * session['inventory_dict'][sku]['SB Cost']

                #fba_dict[asin]['afn-fulfillable-quantity'] = 0
                #fba_dict[asin]['afn-warehouse-quantity'] = 0
                #fba_dict[asin]['afn-total-quantity'] = 0
                #fba_dict[asin]['afn-inbound-shipped-quantity'] = 0
                #fba_dict[asin]['afn-reserved-quantity'] = 0

            #fba_dict[asin]['afn-fulfillable-quantity'] += int(item['afn-fulfillable-quantity'])
            #fba_dict[asin]['afn-warehouse-quantity'] += int(item['afn-warehouse-quantity'])
            #fba_dict[asin]['afn-total-quantity'] += int(item['afn-total-quantity'])
            #fba_dict[asin]['afn-inbound-shipped-quantity'] += int(item['afn-inbound-shipped-quantity'])
            #fba_dict[asin]['afn-reserved-quantity'] += int(item['afn-reserved-quantity'])

        fba_dict['meta'] = {
        'sku_count':sku_count,
        'total_stock':total_stock,
        'stock_value': stock_value
        }


        session['fba_dict'] = fba_dict

    ## Additional Datatypes

    def SupplierDictBuilder(self):
        cursor = mongo.db.suppliers.find({'owner':session['email']})

        supplier_dict = {}
        count = 0
        average_cost = 0
        average_lead_time = 0
        average_first_cost = 0

        for supplier in cursor :
            supplier_dict[count]={
            '_id':supplier['_id'],
            'name':supplier['name'],
            'email':supplier['email'],
            'lead_time':supplier['lead_time'],
            'default_cost':supplier['default_cost'],
            'default_first_cost':supplier['default_first_cost'],
            'notes':supplier['notes']
            }

            count +=1
            average_cost += supplier['default_cost']
            average_lead_time += supplier['lead_time']
            average_first_cost += supplier['default_first_cost']

        session['supplier_dict'] = supplier_dict

        session['top_bar']['supplier_count'] = count
        if count > 0:
            session['top_bar']['supplier_average_cost'] = average_cost / count
            session['top_bar']['supplier_average_first_cost'] =  average_first_cost / count
            session['top_bar']['supplier_average_lead_time'] = average_lead_time / count


    def CustomerDictBuilder(self):
        pass

    def ShipmentDictBuilder(self) :

        session['shipment_timeout'] = now


        cursor = mongo.db.shipments.find({'$and':[
        {'createDate':{'$lte': session['end'], '$gte':session['start']}},
        {'owner':session['email']}]})

        ship_dict = {}
        for shipment in cursor :
            shipment_id = shipment['shipmentId']
            ship_dict[shipment_id] = {}
            sd = ship_dict[shipment_id]
            for key in shipment.keys():
                sd[key] = shipment[key]





            #handle_time = shipment['createDate'] - shipment['orderDate']
            # handle_time > 0 :
            #days = handle_time.days
            #seconds = handle_time.total_seconds()
            #hours = seconds // 3600
            #hours = hours - (days*24)
            #minutes = (seconds % 3600) // 60
            #seconds = seconds % 60
            #sd['handle_time_string'] = '{} days, {} hours'.format(days, hours)

            #row.append(handle_time_string)

            #if 'actual_delivery_date' in ship_dict[shipment].keys() :
            #    shipping_time = ship_dict[shipment]['actual_delivery_date'] - create_date
            #    shipping_times.append(shipping_time)
            #else :
            #    shipping_time = 'NA'

        session['shippment_timeout'] = now

        session['ship_dict'] = ship_dict


##Depreciated with strip microseconds

## To be depreciated

def ItemChartBuilder(cursor, date_dict, alias_dict, item_sku):
    shipped_to_amz = 0
    item_chart = []
    sku_list = []
    for order in cursor :
        # Sum Order Totals
        if order['orderStatus'] == 'cancelled':
            continue

        order_date = order['orderDate']
        amz_loc = ['24208 SAN MICHELE RD','900 PATROL RD','10240 OLD DOWD RD','705 BOULDER DR','6835 W BUCKEYE RD']
        # Count ship to Amz Quantity to avoid
        ship_to = order['shipTo']['name']

        ship_add = order['shipTo']['street1'].upper()
        #flash(ship_add)

        #.find('AMAZON') >-1 or ship_to.find('GOLDEN STATE') >-1:
        ship_to = order['shipTo']['name'].upper()

        if ship_add in amz_loc :
            for item in order['items'] :
                sku = item['sku']
                if sku == item_sku or item_sku == 'All':
                    shipped_to_amz += item['quantity']
                # Do nothing with these duplicate quantities
            continue

        # Count item qty & sales
        # Join on product Alias
        for item in order['items']:
            row = []

            sku = item['sku']

            if sku in alias_dict :
                sku = alias_dict[sku]

            ## To be implemented ##

            if sku == "":
                continue

            name = item['name']
            qty = item['quantity']
            sales = item['unitPrice']*qty

            store_id = order['advancedOptions']['storeId']

            if sku in sku_list :
                sku_idx = sku_list.index(sku)
                item_chart[sku_idx][2] += sales
                item_chart[sku_idx][3] += qty
                if sku == item_sku or item_sku == 'All':
                    date_dict[order_date.year][order_date.month][order_date.day]+= sales

            else :
                row.append(sku)
                row.append(name)
                row.append(sales)
                row.append(qty)
                sku_list.append(sku)
                item_chart.append(row)
                if sku == item_sku or item_sku == 'All' :
                    date_dict[order_date.year][order_date.month][order_date.day]+= sales

    #Build chart value list
    values=[]

    for year in date_dict.values():
        for month in year.values():
          for day in month.values() :
              values.append(day)

    return item_chart, values, shipped_to_amz

def AliasDictBuilder(cursor):
    ## Alias Dictionary can be built based on context
    ## For example, a single sku or all Owner Alias, etc
    alias_dict = {}
    for alias in cursor :
        alias_dict[alias['Alias']] = alias['Product SKU']

#    cursor.rewind()

#    alias_dict['reverse'] = {}
#    reverse = alias_dict['reverse']
#    for alias in cursor :
#        product_sku = alias['Product SKU']
#        alias = alias['Alias']
#        if alias in reverse :
#        alias_dict['reverse'][alias['Alias']]

    return alias_dict
def FBADictBuilder() :
    cursor = mongo.db.fba.find({'Owner':email})
    fba_dict = {}
    for item in cursor :
        asin = item['asin']

        if asin not in fba_dict :
            fba_dict[asin] = {}

            fba_dict[asin]['afn-fulfillable-quantity'] = 0
            fba_dict[asin]['afn-warehouse-quantity'] = 0
            fba_dict[asin]['afn-total-quantity'] = 0
            fba_dict[asin]['afn-inbound-shipped-quantity'] = 0
            fba_dict[asin]['afn-reserved-quantity'] = 0

        fba_dict[asin]['afn-fulfillable-quantity'] += int(item['afn-fulfillable-quantity'])
        fba_dict[asin]['afn-warehouse-quantity'] += int(item['afn-warehouse-quantity'])
        fba_dict[asin]['afn-total-quantity'] += int(item['afn-total-quantity'])
        fba_dict[asin]['afn-inbound-shipped-quantity'] += int(item['afn-inbound-shipped-quantity'])
        fba_dict[asin]['afn-reserved-quantity'] += int(item['afn-reserved-quantity'])

    return fba_dict
### Duplicated Into Stockboy class
def DateDictBuilder(start,end):
    ## Build date range dictionary
    ## Documentation at: https://gist.github.com/jasonhdavis/73664baf24bdb595ffe0b66d01703c01
    date_dict={}
    labels = []
    date_dict.update({start.year:{start.month:{start.day: 0}}})
    delta_range = (end - start).days
    y_keys = [str(start.year)]
    m_keys = [str(start.month)+"-"+str(start.year)]

    for i in range(delta_range+1):
        iter_date = start+timedelta(days=i)
        year = iter_date.year
        month = iter_date.month
        day = iter_date.day
        labels.append(iter_date.strftime('%m-%d'))
        if str(year) in y_keys and str(month)+"-"+str(year) in m_keys :
            date_dict[year][month][day] = 0
        elif str(year) in y_keys :
            date_dict[year].update({month:{day:0}})
            m_keys.append(str(month)+"-"+str(year))
        else :
            date_dict.update({year:{month:{day:0}}})
            y_keys.append(str(year))
            m_keys.append(str(month)+"-"+str(year))

    return date_dict, labels
def ShipmentDictBuilder(start, end) :
    cursor = mongo.db.shipments.find({'$and':[
    {'createDate':{'$lte': end, '$gte':start}},
    {'owner':session['email']}]})

    ship_dict = {}
    for item in cursor :
        shipment_id = item['shipmentId']

        ship_dict[shipment_id] = {}
        sd = ship_dict[shipment_id]
        sd['orderId'] = item['orderId']
        sd['shipmentCost'] = item['shipmentCost']
        sd['name'] = item['shipTo']['name']
        sd['createDate'] = item['createDate']
        sd['carrierCode'] = item['carrierCode']
        sd['trackingNumber'] = item['trackingNumber']
        sd['warehouseId'] = item['warehouseId']
        sd['userId'] = item['userId']



    return ship_dict
def ShipperDictBuilder() :
    shipper_dict = {}

    cursor = mongo.db.users.find() #This is a very troublesome collection namespace
    ## This should be update to ss_users

    for shipper in cursor :
        shipper_dict[shipper['userId']] = {}
        sd = shipper_dict[shipper['userId']]
        sd['name']=shipper['name']
        sd['owner']=shipper['owner']
        sd['username']=shipper['userName']

    return shipper_dict


class MongoRole(enginedb.Document, RoleMixin):
    name = enginedb.StringField(max_length=80, unique=True)
    description = enginedb.StringField(max_length=255)

class MongoUser(enginedb.Document, UserMixin):
    email = enginedb.EmailField()
    password = enginedb.StringField(max_length=255)
    active = enginedb.BooleanField(default=True)
    confirmed_at = enginedb.DateTimeField()
    roles = enginedb.ListField(enginedb.ReferenceField(MongoRole), default=[])
    ss_key = enginedb.StringField(max_length=255)
    ss_secret = enginedb.StringField(max_length=255)
    ss_lastupdated = enginedb.DateTimeField()
    inventory_updated = enginedb.StringField()
    alias_updated = enginedb.DateTimeField()
    fba_updated = enginedb.DateTimeField()
    fba_merchant_id = enginedb.StringField(max_length=255)
    fba_access_key = enginedb.StringField(max_length=255)
    fba_secret_key = enginedb.StringField(max_length=255)
    current_login_at = enginedb.DateTimeField()
    current_login_ip = enginedb.StringField(max_length=255)
    login_count = enginedb.IntField()
# Setup Flask-Security
#user_datastore = SQLAlchemyUserDatastore(db, User, Role)
#security = Security(app, user_datastore)
user_datastore = MongoEngineUserDatastore(enginedb, MongoUser, MongoRole)
security = Security(app, user_datastore)


###############
### FORMS #####
###############

class SSAPI(FlaskForm):
    user = HiddenField()
    key = TextField(validators.DataRequired())#current_user.ss_key)
    secret = PasswordField(validators.DataRequired())#current_user.ss_secret)
    submit = SubmitField('Submit')

class MWSAPI(FlaskForm):
    user = HiddenField()
    merchant = TextField(validators.DataRequired())#current_user.ss_key)
    key = TextField(validators.DataRequired())#current_user.ss_key)
    secret = PasswordField(validators.DataRequired())#current_user.ss_secret)
    submit = SubmitField('Submit')

class FileUploadForm(FlaskForm) :
    file = FileField(validators=[FileRequired()])
    submit = SubmitField('Upload')

class InventoryCostForm(FlaskForm):
    cost = DecimalField(places=2)
    sku = HiddenField()

class SupplierForm(FlaskForm):
    id = HiddenField()
    name = TextField(validators.DataRequired())
    email = TextField()
    lead_time = IntegerField()
    default_cost = FloatField()
    default_first_cost = FloatField()
    notes = TextAreaField()
    moq = IntegerField()
    submit = SubmitField('Add Supplier')


class SupplierSelect(FlaskForm):
    product_id = HiddenField(validators.DataRequired())
    supplier = SelectField()
    supplier_id = TextField()
    model_no = TextField()
    lead_time = IntegerField()
    moq = IntegerField()
    notes = TextAreaField()


#########################
### Form Contollers #####
#########################


def SSAPIForm(ss_api_form):
        flash('ShipStation API Key Updated - Data will begin showing shortly')
        userid = current_user.id
        current = mongo.db.mongo_user.find_one({"_id": ObjectId(userid)})
        current['ss_key'] = ss_api_form.key.data
        current['ss_secret'] = ss_api_form.secret.data
        mongo.db.mongo_user.update({"_id":ObjectId(userid)},{'$set':{'ss_key':ss_api_form.key.data, 'ss_secret':ss_api_form.secret.data}},upsert=True)
        #current_user.ss_key = apiform.key.data
        #current_user.ss_secret = apiform.secret.data

def InventoryFormController(inventoryform):
    f = inventoryform.file.data
    filename = secure_filename(f.filename)
    save_path = os.path.join(app.root_path,'uploads', filename)
    f.save(save_path)
    wb = xlrd.open_workbook(save_path)
    ws = wb.sheet_by_index(0)
    session['upload_status'] = False
    if ws.cell_value(0,0) != 'Inventory Status Report' :
        flash('File does not match expected Inventory Stock Report format')
    else:
        #mongo.db.inventory.remove({"Owner":email})
        flash('File Accepted')
        col_len = len(ws.col(0))
        r = 8
        update_count = 0
        while r < col_len :
            sku = str(ws.cell_value(r,1))
            name = ws.cell_value(r,2).encode('utf8')
            if sku == "":
                #Shipstation merges name column vertically
                # Leaving  blank sku cells below
                r = r+1
                continue
            row = {}
            row['SKU'] = sku
            row['Product Name'] = name
            row['Last Cost'] = ws.cell_value(r,3)
            row['Avg Cost'] = ws.cell_value(r,4)
            row['Stock'] = ws.cell_value(r,6)
            row['Allocated'] = ws.cell_value(r,7)
            row['Available'] = ws.cell_value(r,8)
            row['Reorder Threshold'] = ws.cell_value(r,9)
            row['Owner'] = session['email']
            mongo.db.inventory.update({'SKU':str(sku)},{'$set': row},upsert=True)
            r = r+1
            update_count+=1
        flash('Imported '+ str(update_count) + ' Inventory Stock Values')
        mongo.db.mongo_user.update({'email':session['email']},{'$set':{'inventory_updated':nice_now}})
        session['upload_status'] = True

def AliasUploadController(aliasform):

    f = aliasform.file.data
    filename = secure_filename(f.filename)
    save_path = os.path.join(app.root_path,'uploads', filename)
    f.save(save_path)
    wb = xlrd.open_workbook(save_path)
    ws = wb.sheet_by_index(0)
    col_len = len(ws.col(0))
    r = 2
    if ws.cell_value(0,2) != 'Store Name':
        flash('Uploaded File does not match expected Alias Report Format')
    else:
        mongo.db.alias.remove({"Owner":session['email']})

        while r < col_len :
            row = {}
            row['Product SKU'] = str(ws.cell_value(r,0))
            row['Alias'] = str(ws.cell_value(r,1))
            row['Store Name'] = str(ws.cell_value(r,2))
            row['Store ID'] = str(ws.cell_value(r,3))
            row['Owner'] = session['email']
            mongo.db.alias.update({'Alias':str(ws.cell_value(r,1))},row,upsert=True)
            r+=1
        flash('Imported ' + str(col_len-2) + ' Alias Records')

        mongo.db.mongo_user.update({'email':session['email']},{'$set':{'alias_updated':now}})

###############
### VIEWS #####
###############

class DashboardView(AdminIndexView):
    @expose('/', methods=('GET', 'POST'))
    @login_required
    @CheckUserFeatures
    def index(self):
        if request.method == 'POST':
            session['dateformvalue'] = request.form.get('daterange')

        sb = StockBoy()

        sb.DateFormController()
        sb.OrderCacheAndCursor()
        #flash('Orders Built')

        sku_sales_sort = []
        for item in session['sku_sales_dict'].values() :
            sku_sales_sort.append((item['sku'],item['sales']))

        sku_sales_sort.sort(key=itemgetter(1), reverse=True)
        sku_sales_sort = sku_sales_sort[0:5]

        session['sku_sales_sort'] = sku_sales_sort



        session['sku_sales_sort'] = sku_sales_sort
        return self.render('admin/index.html')

class ProfileView(BaseView):
    @expose('/', methods=('GET','POST'))
    @login_required
    def UserProfile(self):
        sb = StockBoy()
        fbaform = MWSAPI(prefix='fbaform')
        inventoryform = FileUploadForm(prefix='inventoryform')
        aliasform = FileUploadForm(prefix='aliasform')

        apiform = SSAPI(prefix='apiform')
        if request.method == 'POST' and apiform.submit.data:
            SSAPIForm(apiform)

        if inventoryform.validate_on_submit() and inventoryform.submit.data:
            InventoryFormController(inventoryform)
            return redirect(url_for('import.UserProfile'))

        if aliasform.validate_on_submit() and aliasform.submit.data:
            AliasUploadController(aliasform)
            return redirect(url_for('import.UserProfile'))

        if request.method == 'POST' and fbaform.submit.data:
            flash('MWS API Key Updated - Data will be available within 24 hours.')
            userid = current_user.id
            current = mongo.db.mongo_user.find_one({"_id": ObjectId(userid)})
            current['fba_merchant_id'] = fbaform.merchant.data
            current['fba_access_key'] = fbaform.key.data
            current['fba_secret_key'] = fbaform.secret.data
            mongo.db.mongo_user.update({"_id":ObjectId(userid)},{'$set':{'fba_merchant_id':fbaform.merchant.data,'fba_access_key':fbaform.key.data, 'fba_secret_key':fbaform.secret.data}},upsert=True)
            #current_user.ss_key = apiform.key.data
            #current_user.ss_secret = apiform.secret.data
            return redirect(url_for('import.UserProfile'))

        return self.render('admin/user_profile.html', inventoryform=inventoryform, aliasform=aliasform, apiform=apiform, fbaform=fbaform)

class Settings(BaseView):
    @expose('/', methods=('GET','POST'))
    @login_required
    def User(self):
        sb = StockBoy()

        return self.render('admin/settings.html')

    @expose('/billing/', methods=('GET','POST'))
    @login_required
    def BillingView(self):

        sb = StockBoy()
        charges= stripe.Charge.list()

        sb.ExpireCache('init_timeout')
        sb.ExpireCache('alias_timeout')
        sb.ExpireCache('orders_timeout')
        sb.ExpireCache('inventory_timeout')
        sb.ExpireCache('fba_timeout')

        return self.render('admin/billing.html', charges=charges)

    @expose('/setup/', methods=('GET','POST'))
    @login_required
    def SetupView(self):
        sb = StockBoy()
        ss_api_form = SSAPI(prefix='apiform')
        if ss_api_form.validate_on_submit() and ss_api_form.submit.data:
            if ss_api_form.key.data and ss_api_form.secret.data :
                SSAPIForm(ss_api_form)
                return redirect(url_for('settings.StepTwoView'))
            else:
                flash('Please complete the api form')

        return self.render('admin/setup.html', ss_api_form=ss_api_form)

    @expose('/setup_step2/', methods=('GET','POST'))
    @login_required
    def StepTwoView(self):
        sb = StockBoy()
        inventoryform = FileUploadForm(prefix='inventoryform')
        if 'inventoryform-file' not in request.files:
            flash('No file Sent')
        else:
            InventoryFormController(inventoryform)
            flash(session['upload_status'])
            if session['upload_status']:
                flash('Success!')
                return redirect(url_for('settings.StepThreeView'))
            else :
                flash('Upload Failed, Please try again')
                return redirect(url_for('settings.StepTwoView'))

        return self.render('admin/setup_step2.html', inventoryform=inventoryform)

    @expose('/setup_step3/', methods=('GET','POST'))
    @login_required
    def StepThreeView(self):
        sb = StockBoy()
        aliasupload = FileUploadForm(prefix='aliasupload')

        if 'aliasupload-file' not in request.files:
            flash('No file Sent')
            flash(request.files)
        else:
            AliasUploadController(aliasupload)
            if session['upload_status']:
                flash("Give us about an hour to get your account ready. We'll Email you when it's done.")
                flash("Filling out billing information now is optional, but recommended.")
                return redirect(url_for('settings.BillingView'))
            else :
                return redirect(url_for('settings.StepThreeView'))

        return self.render('admin/setup_step3.html', aliasupload=aliasupload)

class Sales(BaseView):
    @expose('/', methods=('GET', 'POST'))
    @login_required
    def SalesOverview(self):
        if request.method == 'POST':
            session['dateformvalue'] = request.form.get('daterange')

        sb = StockBoy()
        sb.DateFormController()

        sb.OrderCacheAndCursor()

        return self.render('admin/sales_index.html')

    @expose('/channels/', methods=('GET', 'POST'))
    @login_required
    def Channels(self):
        if request.method == 'POST':
            session['dateformvalue'] = request.form.get('daterange')

        sb = StockBoy()
        sb.DateFormController()
        sb.OrderCacheAndCursor()

        return self.render('admin/sales_channels.html')

    @expose('/<int:store>',methods=('GET', 'POST'))
    @login_required
    def Channel(self, store):
        if request.method == 'POST':
            session['dateformvalue'] = request.form.get('daterange')

        sb = StockBoy()
        sb.DateFormController()
        sb.OrderCacheAndCursor()




        results = sb.results

        return self.render('admin/index.html', results=results)

    @expose('/cogs/', methods=('GET', 'POST'))
    @login_required
    def COGS(self):
        if request.method == 'POST':
            session['dateformvalue'] = request.form.get('daterange')

        sb = StockBoy()
        sb.DateFormController()
        sb.OrderCacheAndCursor()

        return self.render('admin/sales_cogs.html')

    @expose('/orders/', methods=('GET', 'POST'))
    @login_required
    def OrdersOverview(self):
        if request.method == 'POST':
            session['dateformvalue'] = request.form.get('daterange')

        sb = StockBoy()

        sb.DateFormController()

        sb.OrderCacheAndCursor()

        return self.render('admin/sales_orders.html')

    @expose('/orders/<int:orderId>', methods=(['GET']))
    @login_required
    def OrderById(self, orderId):

        sb = StockBoy()

        if str(orderId) not in 'orders_dict'  :
            #flash('Session cached')
            cursor = mongo.db.orders.find(
            {'$and':
            [{'orderId':orderId},
            {'owner':session['email']} ]
            })


            sb.ReportDictBuilder(cursor)
            #flash('Orders Built')
            ## Expire the cache, because this is an a-typical call
            sb.ExpireCache('orders_timeout')
        else:
            pass
            #flash ('Orders Cached')

        return self.render('admin/sales_orderid.html', orderId=orderId)

class InventoryView(BaseView):
    @expose('/',methods=(['GET','POST']))
    @login_required
    def index(self):

        sb = StockBoy()
        costform = InventoryCostForm(prefix='costform')
        session['dateformvalue'] = default_range

        if costform.validate_on_submit():
            #mongo.mongo_user.update({'email':user['email']},{"$set":{'ss_lastupdated':now}},upsert=True)
            if costform.cost.data != 0.00 :
                mongo.db.inventory.update({'SKU':costform.sku.data},{"$set":{'SB Cost':float(costform.cost.data)}})

                # Rebuild Inventory Dict before telling the user saved
                sb.SSInventoryBuilder()
                sb.ExpireCache('inventory_timeout')
                sb.ExpireCache('orders_timeout')
                sb.ExpireCache('fba_timeout')

                return jsonify(data={'message': 'Cost is {}'.format(costform.cost.data)})




        sb.OrderCacheAndCursor()
        sb.InventoryResultsBuilder()


        return self.render('admin/inventory_index.html', costform=costform)

    @expose('/suppliers/', methods=('GET','POST'))
    def SupplierOverview(self):
        supplierform = SupplierForm(prefix='supplierform')
        sb = StockBoy()
        sb.SupplierDictBuilder()

        if request.method == 'POST' and supplierform.submit.data:

            supplier = {
            'name': supplierform.name.data,
            'email': supplierform.email.data,
            'lead_time' : supplierform.lead_time.data,
            'default_cost' : supplierform.default_cost.data,
            'default_first_cost' : supplierform.default_first_cost.data,
            'notes': supplierform.notes.data,
            'owner': session['email']
            }
            mongo.db.suppliers.insert(supplier)
            flash('Supplier ' + supplierform.name.data + ' Added')
            sb.SupplierDictBuilder()


        return self.render('admin/supplier_index.html', supplierform=supplierform)

    @expose('/suppliers/delete/<string:id>', methods=('GET','POST'))

    def SupplierDelete(self, id):
        _id = ObjectId(id)
        mongo.db.suppliers.remove({'_id':_id})

                    # Rebuild Inventory Dict before telling the user saved
        sb = StockBoy()

        sb.SupplierDictBuilder()
        flash('Supplier Deleted')

        return redirect(url_for('inventory.SupplierIndex'))

class ProductView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    @login_required
    def ProductOverview(self) :
        if request.method == 'POST':
            session['dateformvalue'] = request.form.get('daterange')
        else:
            dateformvalue = default_range

        sb = StockBoy()
        sb.DateFormController()
        sb.OrderCacheAndCursor()


        return self.render('admin/product_index.html')

    @expose('/<string:target_sku>',methods=('GET', 'POST'))
    def Product(self, target_sku):
        if request.method == 'POST':
            session['dateformvalue'] = request.form.get('daterange')
        else:
            formvalue = session['dateformvalue']

        sb = StockBoy()
        sb.results['target_sku'] = target_sku

        supplierselect = SupplierSelect(prefix='supplierselect')
        supplierselect.supplier.choices=[('Null','Select One')]
        #default_tupple = ('Null','Select One')
        for supplier in session['supplier_dict'].values() :
            supplierselect.supplier.choices += [(str(supplier['_id']), supplier['name'])]


        if request.method == 'POST':
            formvalue = request.form.get('daterange')


        ## Use this as an instance variable, pass via class
        sb.OrderCacheAndCursor()
        sb.InventoryResultsBuilder()
        #sb.OrderedTogether(target_sku)
        inv_stock_value = 0
        item_cost = 0
        available = 0

        if target_sku in session['inventory_dict'] :
            inv_details = session['inventory_dict'][target_sku]
            available = session['inventory_dict'][target_sku]['Available']
            if 'SB Cost' in inv_details :
                item_cost = inv_details['SB Cost']
            elif inv_details['Last Cost'] > inv_details['Avg Cost']:
                item_cost = inv_details['Last Cost']
            else :
                item_cost = inv_details['Avg Cost']

            inv_stock_value = session['inventory_dict'][target_sku]['Stock Value']

        if target_sku in session['fba_dict'] :
            fba_stock = session['fba_dict'][target_sku]['InStockSupplyQty']
        else :
            fba_stock = 0

        total_stock_value = (item_cost*fba_stock) + inv_stock_value

        session['top_bar']['total_stock_value'] = total_stock_value
        total_stock = fba_stock + available
        session['top_bar']['total_stock'] = total_stock

        if target_sku in session['sku_sales_dict'] :
            if session['sku_sales_dict'][target_sku]['burn'] > 0:
                session['top_bar']['days'] = total_stock / session['sku_sales_dict'][target_sku]['burn']
            else :
                session['top_bar']['days'] = 9999.9
        else :
            session['top_bar']['days'] = 9999.9

        #session['results'] = jsonify(sb.results)
        #return self.render('admin/index.html', results=results)

        return self.render('admin/product_sku.html', sku=target_sku, supplierselect=supplierselect)


    @expose('/supplier-details/edit',methods=(['POST']))
    def EditProduct(self):
        supplierselect = SupplierSelect(prefix='supplierselect')
        sb=StockBoy()
        #if supplierselect.validate_on_submit():
        id = supplierselect.product_id.data
        id = ObjectId(id)

        results = {}

        if supplierselect.supplier.data :
            if supplierselect.supplier.data != 'Null':
                results['supplier_id']= supplierselect.supplier.data
                for supplier in session['supplier_dict'].values():
                    if str(results['supplier_id']) == str(supplier['_id']):
                        results['supplier_name'] = supplier['name']
        if supplierselect.model_no.data :
            results['model_no']= supplierselect.model_no.data
        if supplierselect.lead_time.data :
            results['lead_time']= supplierselect.lead_time.data
        if supplierselect.moq.data :
            results['moq']= supplierselect.moq.data
        if supplierselect.notes.data:
            results['notes']= supplierselect.notes.data

        mongo.db.products.update(
        {'_id':id},
        {'$set': results }
        )

        sb.ProductDictBuilder()


        return jsonify(data={'message':supplierselect.supplier.data })

    @expose('/barcode/<string:target_sku>', methods=('GET','POST'))
    def BarcodePrint(self, target_sku):

        sb = StockBoy()

        return self.render('admin/barcode_popup.html', sku=target_sku)

class FBAView(BaseView):
    @expose('/',methods=(['GET']))
    @login_required
    def index(self):
        sb = StockBoy()

        ## Calculate Turn every X days (future user setting)
        session['dateformvalue'] = False
        ## Target FBA days of inventory (future user setting)
        fba_target = 30
        ## Target Re-send frequency (future user setting)
        fba_resend = 15
        ## Minimum inventory count
        fba_minimum = 6

        sb.DateFormController()
        #temporarily resolve transfer to session from results
        #session['delta_range'] = sb.results['delta_range']


        if sb.IsExpired('orders_timeout') or not session['processed']:
            #flash('Session cached')

            cursor = mongo.db.orders.find(
            {'$and':[
                {'orderDate':
                    {'$lte': session['end'],
                    '$gte':session['start']}
                },
                {'owner':session['email']}
            ]}
            )
            sb.ReportDictBuilder(cursor)

            #flash('Orders Built')
        else :
            #flash('Orders Cached')
            #return self.render('admin/fba_index.html')
            pass


        #sb.ProductDictBuilder()
        fba_dict = session['fba_dict']
        product_dict = session['product_dict']

        ## Final Result
        sku_sales_dict = session['sku_sales_dict']
        ## | UPC | Name | Amazon Qty (45 Days) | FBA Inventory | Days

        fba_inventory_results_dict = {}
        total_qty = 0
        total_cost = 0
        days_list = []
        #fba_skus = len(fba_dict)


        for item in fba_dict :
            if item == 'meta':
                continue
            # Match ASIN with SKU
            sku = sb.SKUFlatten(item)
            # Get Amazon Only Sales from SKU Sales Dict
            name = None
            if sku in product_dict :
                name = product_dict[sku]['name']

            if sku in sku_sales_dict :

                amz_qty = int(sku_sales_dict[sku]['amz-qty'])
                total_qty += amz_qty
                supply = int(fba_dict[item]['InStockSupplyQty'])
                amz_sales = sku_sales_dict[sku]['amz-sales']
                if not name :
                    name = sku_sales_dict[sku]['name']
                img = sku_sales_dict[sku]['img']

                if amz_qty == 0 :
                    days = 9999
                    recommended = 0

                else :
                    if supply == 0 :
                        days = 0

                    else :
                        days = float(supply) / (float(amz_qty)/float(default_range))

                    total_target = fba_target + fba_resend
                    ratio = float(total_target) / float(default_range)

                    recommended = int(ratio * amz_qty) - supply

                    if recommended <= 0 :
                        recommended = 0


            else :
                amz_qty = 0
                supply = int(fba_dict[item]['InStockSupplyQty'])
                days = 9999
                amz_sales = 0
                img = '\#'
                recommended = 0

                if not name :
                    #name = 'Unable to determine product name'
                    name = 'Unable to determine product name'
                    if 'Title' in fba_dict[item]:
                        name = fba_dict[item]['Title']
                        img = fba_dict[item]['img']


            if supply == 0 and name == 'Unable to determine product name':
                continue
            else:
                fba_inventory_results_dict[sku] = {
                    'sku' : sku,
                    'asin' : item,
                    'name' : name,
                    'img' : img,
                    'amz-sales' : amz_sales,
                    'amz-qty': amz_qty,
                    'supply' : supply,
                    'days': days,
                    'recommended': recommended
                }

        session['fba_inventory_results_dict'] = fba_inventory_results_dict

        if total_qty > 0:
            session['top_bar']['fba_avg_days'] = (float(session['fba_dict']['meta']['total_stock']) / float(total_qty))*default_range

        else :
            session['top_bar']['fba_avg_days'] = 0

        return self.render('admin/fba_index.html')

class CustomerView(BaseView) :
    @expose('/',methods=('GET', 'POST'))
    @login_required
    def CustomerIndex(self) :
        if request.method == 'POST':
            session['dateformvalue'] = request.form.get('daterange')

        sb = StockBoy()
        sb.DateFormController()
        sb.OrderCacheAndCursor()

        return self.render('admin/customer_index.html')

    @expose('/<customer_id>',methods=('GET', 'POST'))
    def CustomerDetails(self, customer_id):

        # Query orders for customer ID
        # Build sales & item chart based on query

        email = session['email']
        alias_search = mongo.db.alias.find({'Owner':email})
        alias_dict = AliasDictBuilder(alias_search)

        try:
            customer_id = int(customer_id)
        except:
            pass

        customer_search = mongo.db.orders.find({'customerId':customer_id})

        num_orders = customer_search.count()
        customer_order_history = []
        customer_dict = {
            'customer_value' : 0
        }
        for order in customer_search :
            customer_order_history.append(order['orderDate'])
            customer_dict['name'] = order['shipTo']['name']
            customer_dict['street1'] = order['shipTo']['street1']
            customer_dict['city'] = order['shipTo']['city']
            customer_dict['state'] = order['shipTo']['state']
            customer_dict['email'] = order['customerEmail']
            customer_dict['customer_value'] += order['orderTotal']


        customer_search.rewind()
        customer_since = min(customer_order_history)
        #customer_since = datetime.strptime(customer_since,'%Y-%m-%dT%H:%M:%S')

        item_sku= 'All'

        start = customer_since
        end = today
        delta_range = (end - start).days

        date_dict, labels = DateDictBuilder(start, end)

        item_chart, values, shipped_to_amz = ItemChartBuilder(customer_search, date_dict, alias_dict, item_sku)
        top_bar = []

        # Customer Value

        # Number of Orders
        # Customer Since
        # Frequency
        order_frequency = delta_range/num_orders

        top_bar.append(customer_dict['customer_value'])
        top_bar.append(num_orders)
        customer_since_date_str = str(customer_since.month) +"/"+ str(customer_since.day) +"/"+ str(customer_since.year)
        top_bar.append(customer_since_date_str)
        top_bar.append(order_frequency)

        return self.render('admin/customer_details.html', top=top_bar, orders=item_chart, labels=labels, values=values, customer_dict=customer_dict)

class ShipmentView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    @login_required
    #def is_visible(self):
        #return False
    def ShipmentOverview(self):

        if request.method == 'POST':
            session['dateformvalue'] = request.form.get('daterange')

        sb = StockBoy()
        sb.DateFormController()

        sb.ShipmentDictBuilder()


        return self.render('admin/shipment_index.html')



######################################
######## HOMEPAGE TO STATIC SITE ####
######################################
# Flask views
index_file_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'homepage')
assets_file_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'homepage/assets')

@app.route('/', methods=['GET'])
def homepage():
    return send_from_directory(index_file_dir, 'index.html')

@app.route('/scan-and-label.html', methods=['GET'])
def scan():
    return send_from_directory(index_file_dir, 'scan-and-label.html')

@app.route('/assets/<path:path>', methods=['GET'])
def serve_file_in_dir(path):

    if not os.path.isfile(os.path.join(assets_file_dir, path)):
        path = os.path.join(index_file_dir, 'index.html')

    path, filename = os.path.split(path)
    request_file_dir = os.path.join(assets_file_dir,path)

    return send_from_directory(request_file_dir, filename)



#### START FLASK ADMIN
### DASHBOARD & LOGIN

admin = flask_admin.Admin(
    app, name='Stockboy',
    base_template='my_master.html',
    template_mode='bootstrap3',
    url = '/dashboard',
    category_icon_classes={
        'Sales': 'fa fa-area-chart',
        'Inventory': 'fa fa-archive',
        },
    index_view=DashboardView(
    name='Dashboard',
    template='admin/index.html',
    menu_icon_type='fa',
    url = '/dashboard',
    menu_icon_value='fa-tachometer',

    )
    #index_view = DashboardView()

)
#################
# ADD ADMIN VIEWS
#################
#admin.add_view(InventoryEditor(Inventory,'Inventory',name='Inventory',menu_icon_type='fa',menu_icon_value='fa-archive'))
admin.add_view(Sales(name="Overview", category='Sales', endpoint='sales', menu_icon_type='fa', menu_icon_value='fa-area-chart'))
admin.add_view(Sales(name="COGS", category='Sales', endpoint='sales/cogs', menu_icon_type='fa', menu_icon_value='fa-cogs'))
admin.add_view(Sales(name="Channels", category='Sales', endpoint='sales/channels', menu_icon_type='fa', menu_icon_value='fa-tasks'))
admin.add_view(Sales(name="Orders", category='Sales', endpoint='sales/orders', menu_icon_type='fa', menu_icon_value='fa-sticky-note-o'))


admin.add_view(InventoryView(name="Overview", endpoint='inventory', menu_icon_type='fa', menu_icon_value='fa-archive', category='Inventory'))
admin.add_view(InventoryView(name="Suppliers", endpoint="inventory/suppliers",  menu_icon_type='fa', menu_icon_value='fa-ambulance', category='Inventory'))
admin.add_view(ProductView(name="Products", endpoint='product', menu_icon_type='fa', menu_icon_value='fa-shopping-bag'))
admin.add_view(CustomerView(name="Customers", endpoint='customers', menu_icon_type='fa', menu_icon_value='fa-users'))
admin.add_view(ShipmentView(name="Shipments", endpoint='shipments', menu_icon_type='fa', menu_icon_value='fa-truck'))
if 'amazon_mws_api' in results :
    admin.add_view(FBAView(name="FBA", endpoint='fba', menu_icon_type='fa', menu_icon_value='fa-amazon'))
#admin.add_view(BurnView(name="Burn", endpoint='burn', menu_icon_type='fa', menu_icon_value='fa-free-code-camp'))
admin.add_view(Settings(name='Settings', endpoint='settings', menu_icon_type='fa', menu_icon_value='fa-cog'))
admin.add_view(ProfileView(name='Import', endpoint='import', menu_icon_type='fa', menu_icon_value='fa-cog'))
admin.add_view(Settings(name='Reset Cache', endpoint='settings/billing', menu_icon_type='fa', menu_icon_value='fa-cog'))

#admin.add_sub_view(SupplierView())

#admin.add_view(InventoryEditor(name='Inventory Editor', endpoint='inventory-editor', menu_icon_type='fa', menu_icon_value='fa-cog'))

## Sub Items - Not visible in menu
# define a context processor for merging flask-admin's template context into the
# flask-security views.
@security.context_processor
def security_context_processor():
    return dict(
        admin_base_template=admin.base_template,
        admin_view=admin.index_view,
        h=admin_helpers,
        get_url=url_for
    )


if __name__ == '__main__':

    # Build a sample db on the fly, if one does not exist yet.
    app_dir = os.path.realpath(os.path.dirname(__file__))
    #database_path = os.path.join(app_dir, app.config['DATABASE_FILE'])


    # Start app
    app.run(debug=True, host='0.0.0.0')
