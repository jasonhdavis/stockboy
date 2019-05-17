#!venv/bin/python
# -*- coding: utf-8 -*-

import os
import time
import csv
import sys
import copy
import pytz

from operator import itemgetter
from datetime import datetime, timedelta
import locale
locale.setlocale(locale.LC_ALL, '')
from flask import Flask, url_for, redirect, render_template, request, abort, flash, send_from_directory, Markup, jsonify
from flask_mongoengine import MongoEngine
from flask_mongoengine.wtf import model_form
from flask_security import Security, \
    UserMixin, RoleMixin, login_required, current_user, datastore, MongoEngineUserDatastore
from flask_security.utils import encrypt_password
import hashlib
import base64
from flask_mail import Mail
import flask_admin
from flask_admin.contrib import sqla
from flask_admin import helpers as admin_helpers
from flask_admin import BaseView, expose, AdminIndexView
from flask_bootstrap import Bootstrap
from flask_datepicker import datepicker
from flask_admin.contrib.pymongo import ModelView
from flask_wtf import Form, FlaskForm
from wtforms import StringField, PasswordField, TextField, SubmitField, validators, HiddenField
from flask_wtf.file import FileField, FileRequired
from werkzeug.utils import secure_filename
from werkzeug.datastructures import CombinedMultiDict
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
import random
from flask_login import LoginManager

from fba_locations import fba_locations


import xlrd

env_version = sys.version_info


# Create Fldask application
app = Flask(__name__)
app.config.from_pyfile('config.py')

Bootstrap(app)
datepicker(app)

mongo = PyMongo(app)
enginedb = MongoEngine(app)
mail = Mail(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

#scheduler = BackgroundScheduler()
#atexit.register(lambda: scheduler.shutdown())
now = datetime.now()
today = datetime(now.year, now.month, now.day, 23,59,59)


######################################
############# FUNCTIONS ##############
######################################
def StripMicroseconds(datestring) :
    output = str(datestring).split('.')
    output = output[0]
    return output

def DateFormHanlder(formvalue):
    if not formvalue :
        start = today - timedelta(days=30, hours=23, minutes=59, seconds=59)
        end = today
    elif type(formvalue) == int :
        start = today - timedelta(days=formvalue)
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


class StockBoy() :

    def __init__(self):
        email = current_user.email #'MeganERager@gmail.com'#
        self.results = {
        'email':email,#'matt@charlieandwobbs.com'#
        'element':0
        }
        #RUN INIT BUILDERS
        self.AliasDictBuilder()
        self.StoreDictBuilder()
        self.ShipperDictBuilder()
        self.ProductDictBuilder()
        self.FBADictBuilder()

    def DateFormController(self, formvalue) :
        start, end = DateFormHanlder(formvalue)
        #end = end.replace(hour=0, minute=01)
        #start = start.replace(hour=23, minute=59)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days

        self.DateDictBuilder(start, end)
        self.results['formavlue'] = formvalue
        self.results['start'] = start
        self.results['end'] = end
        self.results['start_date'] = start_date
        self.results['end_date'] = end_date
        self.results['delta_range'] = delta_range+1

##############################
###### INIT BUILDERS #########
#############################
    def StoreDictBuilder(self):
        store_dict = {}

        cursor = mongo.db.stores.find({
        'owner':self.results['email']
        })

        for store in cursor :
            store_id = store['storeId']
            store_dict[store_id] = store

        self.results['store_dict'] = store_dict

    def ShipperDictBuilder(self) :
        shipper_dict = {}

        cursor = mongo.db.users.find()

        for shipper in cursor :
            shipper_dict[shipper['userId']] = {}
            sd = shipper_dict[shipper['userId']]
            sd['name']=shipper['name']
            sd['owner']=shipper['owner']
            sd['username']=shipper['userName']

        self.results['shipper_dict'] = shipper_dict

    def AliasDictBuilder(self):
        ## Alias Dictionary can be built based on context
        ## For example, a single sku or all Owner Alias, etc
        alias_search = mongo.db.alias.find({'Owner':self.results['email']})

        alias_dict = {}
        for alias in alias_search :
            alias_dict[alias['Alias']] = alias['Product SKU']

        self.results['alias_dict'] = alias_dict

        if len(alias_dict) == 0 :
            flash(Markup('Please Upload Your Product <a href="https://stockboy.co/dashboard/import/#alias-card">Alias List from Shipstation</a>.'))


########################################
###### PRIMARY REPORT BUILDERS #########
########################################
    def ReportDictBuilder(self, order_cursor):

        ### Define a number of outputs that come from iterating through orders
        ### Create all possible dictionaries / outputs at once with a single loop of orders

        #### OUTPUT ########
        ### TOTAL SALES DICTS ######
        category_sales_dict = {}
        store_sales_dict = {}
        sku_sales_dict = {}
        customer_sales_dict = {}
        ordered_together_dict = {}

        ##### BY DAY DICTS ######
        by_day_dict = copy.deepcopy(self.results['date_dict'])

        for year in by_day_dict.values() :
            for month in year.values() :
                for day in month.keys():
                    month[day] = {
                        'sales':0,
                        'qty':0,
                        'fba':0,
                        'discounts':0,
                        'shipping':0,
                        }

        ## Top Bar / Top level metrics
        total_qty = 0
        total_sales = 0
        shipped_to_amz = 0
        total_discounts = 0
        total_shipping = 0
        ### Supporting Variables
        #alias_dict
        ##product_dict

        delta_range = self.results['delta_range']
        store_dict = self.results['store_dict']
        sku_category_dict = self.results['sku_category_dict']


        if 'target_sku' in self.results.keys() :
            target_sku = self.results['target_sku']
        else :
            target_sku = 'All'

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
            store_id = order['advancedOptions']['storeId']
            customer_id = order['customerId']
            warehouse_id = order['advancedOptions']['warehouseId']
            create_date = order['createDate']
            timezone = pytz.timezone("America/Los_Angeles")

            order_date = order['orderDate']
            order_date = timezone.localize(order_date)
            order_id = order['orderId']
            order_status = order['orderStatus']

            if order_status == 'cancelled':
                continue

            order_total = order['orderTotal']
            order_qty = 0
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

            is_amz = False
            amz_transfer = self.AMZCheck(street1, state)


            if store_dict[store_id]['marketplaceName'] == 'Amazon':
                shipping_amt = 0
                is_amz = True

            for item in order['items'] :

                item_sku = self.SKUFlatten(item['sku'])
                ordered_together.append(item_sku)
                if target_sku != 'All' and target_sku != item_sku:
                    continue

                price = item['unitPrice']
                qty = item['quantity']
                img = item['imageUrl']
                name = item['name']

                if price < 0 or 'discount' in name.lower() :
                    if price > 0 :
                        price = price * -1

                    discounts += price
                    calculated_total += price
                    qty = 0 #Do not count discount as an item

                if item_sku in sku_category_dict :
                    category_id = sku_category_dict[item_sku][0]
                    category_name = sku_category_dict[item_sku][1]

                else:
                    category_id = None
                    category_name = 'None'
                ## If sent to Amazon, avoid qty double count

                if amz_transfer :
                    by_day_dict[order_date.year][order_date.month][order_date.day]['fba']+= qty
                    shipped_to_amz+= qty

                    continue

                ## Amazon orders have exited, add to total qty count which gets added to topbar total qty
                order_qty += qty
                calculated_total += qty*price

                ### Items not transfered to Amazon get counted in existing record
                if item_sku in sku_sales_dict :
                    ssd = sku_sales_dict[item_sku]
                    ssd['sku'] = item_sku
                    ssd['sales'] += price*qty
                    ssd['qty']+= qty
                    ssd['burn'] = float(ssd['qty'])/float(delta_range)
                    if is_amz :
                        ssd['amz-sales'] += price*qty
                        ssd['amz-qty'] += qty

                ### Else Create a new record
                else :
                    if is_amz:
                        amz_sales = price*qty
                        amz_qty = qty
                    else :
                        amz_sales = 0
                        amz_qty = 0

                    sku_sales_dict[item_sku] = {
                    'sku': item_sku,
                    'name': name,
                    'category': category_name,
                    'img': img,
                    'sales': price*qty,
                    'qty': qty,
                    'burn': qty/delta_range,
                    'amz-sales': amz_sales,
                    'amz-qty':amz_qty
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
            by_day_dict[order_date.year][order_date.month][order_date.day]['sales']+= calculated_total
            by_day_dict[order_date.year][order_date.month][order_date.day]['qty']+= int(order_qty)
            by_day_dict[order_date.year][order_date.month][order_date.day]['discounts']+= discounts

            ### Calculate Top Level Stats####
            total_qty += order_qty
            # All discounts are negative and were not part of item calculation because their quantity is zero
            total_sales += calculated_total
            total_discounts += discounts
            total_shipping += shipping_amt

            #### STORE / MARKETPLACE CHANNEL SALES ####
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
                customer_sales_dict[customerId]['sales'] += calculated_total
                customer_sales_dict[customerId]['qty' ]+= order_qty


            elif customer_id in vars() :
                customer_sales_dict[customerId]={
                'name': name,
                'sales': calculated_total,
                'qty': order_qty,
                'shipping': shipping_amt,
                'discounts': discounts,
                'street1': street1,
                'city': city,
                'state': state,
                'email': email
                }


        #### STORE RESULTS ####
        # By ID results
        self.results['category_sales_dict'] = category_sales_dict
        self.results['store_sales_dict'] = store_sales_dict
        self.results['sku_sales_dict'] = sku_sales_dict
        self.results['customer_sales_dict'] = customer_sales_dict

        # By Day results
        #self.results['sales_by_day_dict'] = sales_by_day_dict
        #self.results['qty_by_day_dict'] = qty_by_day_dict
        #self.results['fba_qty_by_day_dict'] = fba_qty_by_day_dict
        self.results['by_day_dict'] = by_day_dict

        self.results['ordered_together_dict'] = ordered_together_dict

        #### Calculate Average Burn Rate ####
        #### Take each burn rate and get an average ###
        ### Multiply by delta_range to get sold per sku per time period ###

        burn_rates = []
        for sku in sku_sales_dict :
            burn_rates.append(sku_sales_dict[sku]['burn'])

        if len(burn_rates) > 0:
            avg_burn = (sum(burn_rates)/len(burn_rates))*delta_range
            avg_burn = round(avg_burn,2)
        else :
            avg_burn = 0

        qty_per_day = total_qty/delta_range


        self.results['top_bar'] = {
            'total_qty': total_qty,
            'total_sales': total_sales,
            'shipped_to_amz':shipped_to_amz,
            'avg_burn':  avg_burn,
            'qty_per_day': qty_per_day,
            'discounts': total_discounts,
            'shipping': total_shipping,
            'gross_sales': total_sales+total_shipping
            }

    def OrderedTogether(self, sku):
        ordered_together_dict = self.results['ordered_together_dict']

        ordered_with = {}
        for sku_list in ordered_together_dict.values() :
            if sku in sku_list :
                for product in sku_list :
                    if product != sku :
                        if product in ordered_with :
                            ordered_with[product]+=1
                        else:
                            ordered_with[product] = 1

        self.results['ordered_with'] = ordered_with

##############################
#### DICTIONARY BUILDERS #####
##############################

    def ProductDictBuilder(self):
        product_dict = {}

        range_search = mongo.db.products.find({'owner':self.results['email']})

        #fba_inventory = mongo.db.mws.find({'owner':self.results['email']})

        #ss_inventory = mongo.db.inventory.find({'owner':self.results['email']})

        for product in range_search :
            product_id = product['productId']
            product_dict[product_id]=product

        self.results['product_dict'] = product_dict

        sku_category_dict = {}

        product_dict = self.results['product_dict']


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

            if product['productCategory'] :
                cat_id = product_dict[id]['productCategory']['categoryId']
                cat_name = product_dict[id]['productCategory']['name']
            else :
                cat_id = 999
                cat_name = 'uncategorized'

            sku_category_dict[sku] = (cat_id, cat_name)

        self.results['sku_category_dict'] = sku_category_dict

    def CustomerDictBuilder(self):
        pass

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


        self.results['date_dict'] = date_dict
        self.results['date_range_labels'] = labels

    def FBADictBuilder(self) :
        #build address list
        address_list = []
        for location in fba_locations.values() :
            address=location['address'].upper()
            address = AddressEndingStrip(address)
            #zip = location['zip']
            state = location['state']
            address_list.append(address)

        ## BUILD FBA INVENTORY FROM MWS COLLECTION
        cursor = mongo.db.mws.find({'Owner':current_user.email})
        fba_dict = {}
        for item in cursor :
            asin = item['ASIN']

            if asin not in fba_dict :
                fba_dict[asin] = {}
                fba_dict[asin]['SellerSKU'] = item['SellerSKU']
                fba_dict[asin]['TotalSupplyQty'] = item['TotalSupplyQuantity']
                fba_dict[asin]['Condition'] = item['Condition']

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

        self.results['address_list'] = address_list
        self.results['fba_dict'] = fba_dict

    def ShipmentDictBuilder(start, end) :
        cursor = mongo.db.shipments.find({'$and':[
        {'createDate':{'$lte': end, '$gte':start}},
        {'owner':current_user.email}]})

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

    def ChartValueBuilder(date_dict):
        values=[]

        for year in date_dict.values() :
            for month in year.values() :
              for day in month.values() :
                  values.append(day)

        self.loading['values'] = values

    #############################
    ######## UTILITIES ##########
    #############################

    def SKUFlatten (self, sku):

        alias_dict = self.results['alias_dict']

        if sku in alias_dict:
            sku = alias_dict[sku]

        if not sku :
            sku = 'sb-'+str(random.randint(123456,234567))

        return sku


    def AMZCheck(self, street1, state) :

        amz_address = self.results['address_list']

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
    cursor = mongo.db.fba.find({'Owner':current_user.email})
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
def ShipmentDictBuilder(start, end) :
    cursor = mongo.db.shipments.find({'$and':[
    {'createDate':{'$lte': end, '$gte':start}},
    {'owner':current_user.email}]})

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

    cursor = mongo.db.users.find()

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
    email = enginedb.StringField(max_length=255)
    password = enginedb.StringField(max_length=255)
    active = enginedb.BooleanField(default=True)
    confirmed_at = enginedb.DateTimeField()
    roles = enginedb.ListField(enginedb.ReferenceField(MongoRole), default=[])
    ss_key = enginedb.StringField(max_length=255)
    ss_secret = enginedb.StringField(max_length=255)
    ss_lastupdated = enginedb.DateTimeField()
    inventory_updated = enginedb.DateTimeField()
    alias_updated = enginedb.DateTimeField()
    fba_updated = enginedb.DateTimeField()
    fba_merchant_id = enginedb.StringField(max_length=255)
    fba_access_key = enginedb.StringField(max_length=255)
    fba_secret_key = enginedb.StringField(max_length=255)


# Setup Flask-Security
#user_datastore = SQLAlchemyUserDatastore(db, User, Role)
#security = Security(app, user_datastore)
user_datastore = MongoEngineUserDatastore(enginedb, MongoUser, MongoRole)
security = Security(app, user_datastore)

#class Inventory(enginedb.DynamicDocument, UserMixin):
#    sku = enginedb.StringField(max_length=80)
#    available = enginedb.IntField(min_value=0)
#    avg_cost= enginedb.FloatField(min_value=0)
#    product_name = enginedb.StringField(max_length=512)
#    owner = enginedb.StringField(max_length=64)
#    allocated = enginedb.IntField(min_value=0)
#    threshold = enginedb.IntField(min_value=0)
#    last_cost = enginedb.FloatField(min_value=0)
#    stock = enginedb.IntField(min_value=0)

#InventoryForm = model_form(Inventory)

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



class DashboardView(AdminIndexView):
    @expose('/', methods=('GET', 'POST'))
    @login_required
    def index(self):
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        sb = StockBoy()
        sb.DateFormController(formvalue)

        cursor = mongo.db.orders.find(
        {'$and':[
            {'orderDate':
                {'$lte': sb.results['end'],
                '$gte':sb.results['start']}
            },
            {'owner':sb.results['email']}
        ]}
        )

        sb.ReportDictBuilder(cursor)

        sku_sales_sort = []
        for item in sb.results['sku_sales_dict'].values() :
            sku_sales_sort.append((item['sku'],item['sales']))

        sku_sales_sort.sort(key=itemgetter(1), reverse=True)
        sku_sales_sort = sku_sales_sort[0:5]

        sb.results['sku_sales_sort'] = sku_sales_sort

        results = sb.results

        return self.render('admin/index.html', results=results)


class ProfileView(BaseView):
    @expose('/', methods=('GET','POST'))
    @login_required
    def UserProfile(self):
        apiform = SSAPI(prefix='apiform')
        fbaform = MWSAPI(prefix='fbaform')
        inventoryform = FileUploadForm(prefix='inventoryform')
        aliasform = FileUploadForm(prefix='aliasform')
        nice_now = datetime.strftime(now,"%m-%d-%Y at %I:%M %p")

        if request.method == 'POST' and apiform.submit.data:
            flash('ShipStation API Key Updated - Data will begin showing shortly')
            userid = current_user.id
            current = mongo.db.mongo_user.find_one({"_id": ObjectId(userid)})
            current['ss_key'] = apiform.key.data
            current['ss_secret'] = apiform.secret.data
            mongo.db.mongo_user.update({"_id":ObjectId(userid)},{'$set':{'ss_key':apiform.key.data, 'ss_secret':apiform.secret.data}},upsert=True)
            #current_user.ss_key = apiform.key.data
            #current_user.ss_secret = apiform.secret.data
            return redirect(url_for('import.UserProfile'))

        if inventoryform.validate_on_submit() and inventoryform.submit.data:
            f = inventoryform.file.data
            filename = secure_filename(f.filename)
            save_path = os.path.join(app.root_path,'uploads', filename)
            f.save(save_path)
            wb = xlrd.open_workbook(save_path)
            ws = wb.sheet_by_index(0)
            if ws.cell_value(0,0) != 'Inventory Status Report' :
                flash('File does not match expected Inventory Stock Report format')
            else:
                mongo.db.inventory.remove({"Owner":current_user.email})
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
                    row['Owner'] = current_user.email
                    mongo.db.inventory.update({'SKU':str(sku)},row,upsert=True)
                    r = r+1
                    update_count+=1
                flash('Imported '+ str(update_count) + ' Inventory Stock Values')

                mongo.db.mongo_user.update({'email':current_user.email},{'$set':{'inventory_updated':nice_now}})
            return redirect(url_for('import.UserProfile'))

        if aliasform.validate_on_submit() and aliasform.submit.data:

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
                mongo.db.alias.remove({"Owner":current_user.email})

                while r < col_len :
                    row = {}
                    row['Product SKU'] = str(ws.cell_value(r,0))
                    row['Alias'] = str(ws.cell_value(r,1))
                    row['Store Name'] = str(ws.cell_value(r,2))
                    row['Store ID'] = str(ws.cell_value(r,3))
                    row['Owner'] = current_user.email
                    mongo.db.alias.update({'Alias':str(ws.cell_value(r,1))},row,upsert=True)
                    r+=1
                flash('Imported ' + str(col_len-2) + ' Alias Records')
                mongo.db.mongo_user.update({'email':current_user.email},{'$set':{'alias_updated':now}})
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

class SalesView(BaseView):
    @expose('/', methods=('GET', 'POST'))
    @login_required
    def index(self):
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        start, end = DateFormHanlder(formvalue)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days

        date_dict, labels = DateDictBuilder(start, end)

        email = current_user.email
        ### Queries ###

        #Build Alias Dictionary - all owner alias values
        alias_search = mongo.db.alias.find({'Owner':email})
        alias_dict = AliasDictBuilder(alias_search)

        ## Find Owner Orders in Date Range
        range_search = mongo.db.orders.find({'$and':[
        {'orderDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})

        item_sku = 'All'
        item_chart, values, shipped_to_amz = ItemChartBuilder(range_search, date_dict, alias_dict, item_sku)


        top_bar = []
        sales_total = 0
        qty_total = 0
        # Build top bar values

        for row in item_chart :
            sales_total += row[2]
            qty_total += row[3]

            burn = float(row[3])/(delta_range+1)
            burn = round(burn,2)

            row.append(burn)

        #sales_total = locale.currency(sales_total)
        top_bar.append(sales_total)
        top_bar.append(qty_total)
        top_bar.append(shipped_to_amz)
        burn_rates = []

        for row in item_chart :
            row[2]= locale.currency(row[2])
            burn_rates.append(row[4])

        if len(burn_rates) > 0 :
            avg_burn = sum(burn_rates)/len(burn_rates)
            avg_burn = avg_burn*delta_range
            avg_burn = round(avg_burn,2)
        else :
            avg_burn = 0

        top_bar.append(avg_burn)
        max_values = max(values)

        return self.render('admin/sales_index.html',top=top_bar,orders=item_chart, labels=labels, values=values, daterange=formvalue, startdate= start_date, enddate=end_date)

    @expose('/channels/', methods=('GET', 'POST'))
    @login_required
    def SalesChannels(self):

        #Iter through orders
        #Collect sales into date dict under store ID
        # No item chart

        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        start, end = DateFormHanlder(formvalue)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days

        date_dict, labels = DateDictBuilder(start, end)

        email = current_user.email
        ### Queries ###

        ## Find Owner Orders in Date Range
        range_search = mongo.db.orders.find({'$and':[
        {'orderDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})

        store_dict = {}
        item_sku = 'All'
        store_id_list = []
        for order in range_search :
            store_id = order['advancedOptions']['storeId']
            store_id_list.append(store_id)
            order_date = order['createDate']
            sales=order['orderTotal']
            qty=0
            for item in order['items']:
                qty+=item['quantity']
            #if sku == item_sku or item_sku == 'All':
            if date_dict[order_date.year][order_date.month][order_date.day] == 0:
                date_dict[order_date.year][order_date.month][order_date.day] = {}
                date_dict[order_date.year][order_date.month][order_date.day][store_id] = {'sales':0,'qty':0}
                date_dict[order_date.year][order_date.month][order_date.day][store_id]['sales'] += sales
                date_dict[order_date.year][order_date.month][order_date.day][store_id]['qty']+= qty
            elif store_id in date_dict[order_date.year][order_date.month][order_date.day] :
                date_dict[order_date.year][order_date.month][order_date.day][store_id]['sales']+= sales
                date_dict[order_date.year][order_date.month][order_date.day][store_id]['qty']+= qty
            else :
                date_dict[order_date.year][order_date.month][order_date.day][store_id] = {'sales':0,'qty':0}
                date_dict[order_date.year][order_date.month][order_date.day][store_id]['sales']+= sales
                date_dict[order_date.year][order_date.month][order_date.day][store_id]['qty']+= qty


        store_cursor = mongo.db.stores.find({'storeId':{'$in':store_id_list}})



        for store in store_cursor :
            store_id = store['storeId']
            store_dict[store_id] = {}
            sd = store_dict[store_id]
            sd['label'] = store['storeName']
            sd['marketplaceId'] = store['marketplaceId']
            sd['sales'] = 0
            sd['qty'] = 0


        for year in date_dict.values() :
            for month in year.values():
                for day in month.values():
                    if day == 0 :
                        continue
                    else:
                        for store in day.items():
                            store_id = store[0]
                            store_dict[store_id]['sales']+= store[1]['sales']
                            store_dict[store_id]['qty']+= store[1]['qty']


        item_chart = []
        for store in store_dict.values() :
            row = []
            row.append(store['label'])
            row.append(store['qty'])
            row.append(store['sales'])
            item_chart.append(row)


        top_bar=[]
        top_bar.append(len(store_dict.keys()))

        top_bar.append(0)
        top_bar.append(0)
        top_bar.append(0)

        return self.render('admin/sales_channels.html',top=top_bar,orders=item_chart, labels=labels, values=store_dict, daterange=formvalue, startdate= start_date, enddate=end_date)

    @expose('/<int:store>',methods=('GET', 'POST'))
    def Channel(self, store):
        ## Date Handling
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')
        #{'created':{'$lt':datetime.datetime.now(), '$gt':datetime.datetime.now() - timedelta(days=10)}}
        #return str(mdb)

        sb = StockBoy()
        sb.DateFormController(formvalue)

        cursor = mongo.db.orders.find(
        {'$and':[
            {'createDate':
                {'$lte': sb.results['end'],
                '$gte':sb.results['start']}
            },
            {'owner':sb.results['email']},
            {'advancedOptions.storeId':store}
        ]}
        )

        sb.ReportDictBuilder(cursor)



        results = sb.results

        return self.render('admin/index.html', results=results)


class InventoryView(BaseView):
    @expose('/',methods=(['GET']))
    @login_required
    def InventoryCharts(self):

        sb = StockBoy()
        i = 0
        values = []
        labels = []
        orders= []
        shipped_to_amz = 0
        # Sku, Name, Sales, Qty
        items_chart =    []
        sku_list = []

        top_bar = []
        qty_total = 0
        total_value = 0
        sku_count = 0
        email = current_user.email
        formvalue = False
        start, end = DateFormHanlder(formvalue)
        start = start - timedelta(days=15)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days
        date_dict, labels = DateDictBuilder(start, end)


        #Build Alias Dictionary - all owner alias values
        alias_search = mongo.db.alias.find({'Owner':email})
        alias_dict = AliasDictBuilder(alias_search)

        ## Find Owner Orders in Date Range
        range_search = mongo.db.orders.find({'$and':[
        {'orderDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})

        item_sku = 'All'
        item_sales_chart, values, shipped_to_amz = ItemChartBuilder(range_search, date_dict, alias_dict, item_sku)
        sku_sales_index = []
        total_qty_sold = 0

        for item in item_sales_chart :
            sku_sales_index.append(item[0])
            total_qty_sold += item[3]

        fba_dict = sb.results['fba_dict']

        inventory = mongo.db.inventory.find({'Owner':email})


        #search = Inventory.objects(Owner=email)
        for item in inventory :
            row = []
            amz_qty = 0
            sku_count+=1
            matched_asin = False
            sku = item['SKU']
            alias_list = []
            item_amz_warehouse = 0
            item_amz_inbound = 0

            for k,v in alias_dict.items() :
                if v == sku :
                    alias_list.append(k)

            local_qty = item['Stock']
            qty_total += int(local_qty)
            true_count = 0

            for alias in alias_list :
                if alias in fba_dict :
                    matched_asin = True
                    item_amz_warehouse = int(fba_dict[alias]['TotalSupplyQty'])
                    #item_amz_inbound = int(fba_dict[alias]['afn-inbound-shipped-quantity'])
                    amz_qty = item_amz_warehouse #+ item_amz_inbound
                    qty_total += amz_qty
                    true_count +=1
                    if true_count > 1 :
                        flash('Multiple ASIN Match on: '+ str(item['Product Name']))


            if item['Avg Cost'] > 0 :
                total_value += item['Avg Cost']*(local_qty + amz_qty)

            #else :
                #if "Socks" in item['Product Name'] :
                    ## Placeholder for inventory value
                #    total_value += 1.25*int(local_qty+amz_qty)
                #else :
                    ## Placeholder for inventory value
                #    total_value += .75*int(local_qty+amz_qty)


            row.append(sku)
            if env_version < (3, 0):
                row.append(item['Product Name'])
            else :
                row.append(item['Product Name'])

            row.append(int(item['Stock']))
            row.append(item['Avg Cost'])

            if matched_asin :
                row.append(item_amz_warehouse)
                row.append(item_amz_inbound)
            else :
                row.append(0)
                row.append(0)

            ## FBA

            if sku in sku_sales_index :
                sales_idx = sku_sales_index.index(sku)
                qty_sold = item_sales_chart[sales_idx][3]
            else :
                qty_sold = 0

            row.append(qty_sold)
            burn = float(qty_sold)/float(delta_range)

            if burn == 0:
                inventory_days = 9999999
            else :
                inventory_days = int(local_qty+amz_qty) / burn


            row.append(inventory_days)
            items_chart.append(row)


        top_bar.append(qty_total)
        top_bar.append(round(total_value,2))
        top_bar.append(sku_count)
        sell_through_rate = float(total_qty_sold) / float(total_qty_sold+qty_total)
        top_bar.append(sell_through_rate*100)


        return self.render('admin/inventory_index.html', items_chart=items_chart, top=top_bar)

    @expose('/fba',methods=('GET', 'POST'))
    def FBAInventory(self):
        i = 0
        values = []
        labels = []
        orders= []
        shipped_to_amz = 0
        # Sku, Name, Sales, Qty
        items_chart = []
        sku_list = []

        top_bar = []
        qty_total = 0
        total_value = 0
        sku_count = 0

        formvalue = False
        start, end = DateFormHanlder(formvalue)
        start = start - timedelta(days=15)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days
        date_dict, labels = DateDictBuilder(start, end)

        email = current_user.email

        #Build Alias Dictionary - all owner alias values
        alias_search = mongo.db.alias.find({'Owner':email})
        alias_dict = AliasDictBuilder(alias_search)

        ## Find Owner Orders in Date Range
        range_search = mongo.db.orders.find({'$and':[
        {'orderDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})

        item_sku = 'All'
        item_dict, values, shipped_to_amz = ItemDictBuilder(range_search, date_dict, alias_dict, item_sku)
        total_qty_sold = 0

        fba_inventory = mongo.db.fba.find()

        fba_dict = {}
        for fba_item in fba_inventory :
            asin = fba_item['asin']
            fba_dict[asin]={}
            for k, v in fba_item.iteritems() :
                fba_dict[asin][k] = v

        #inventory = mongo.db.inventory.find({'Owner':email})

        for item in fba_inventory :
            row = []
            amz_qty = 0
            sku_count+=1
            matched_asin = False
            sku = item['SKU']

            for k,v in alias_dict.iteritems() :
                if v == sku :
                    test_asin = k
                    if test_asin in fba_dict :
                        matched_asin = test_asin

            #local_qty = item['Stock']
            #qty_total += int(local_qty)

            if matched_asin :
                amz_qty = int(fba_dict[matched_asin]['afn-fulfillable-quantity'])
                qty_total += amz_qty

            if item['Avg Cost'] > 0 :
                total_value += item['Avg Cost'] * amz_qty
            else :
                total_value += 0
                #if "Socks" in item['Product Name'] :
                    ## Placeholder for inventory value
                #    total_value += 1.25*int(local_qty+amz_qty)
                #else :
                    ## Placeholder for inventory value
                #    total_value += .75*int(local_qty+amz_qty)


            row.append(sku)
            row.append(item['Product Name'].decode('utf8').strip())
            if matched_asin :
                row.append(amz_qty)
                row.append(fba_dict[matched_asin]['afn-inbound-shipped-quantity'])
            else :
                row.append(0)
                row.append(0)

            #Amazon Sales only - may conflate Merchant & Amazon fullfilled
            qty_sold = item_dict[sku]['stores'][226766]
            total_qty_sold += qty_sold

            row.append(qty_sold)
            burn = float(qty_sold)/float(delta_range)

            if burn == 0:
                inventory_days = 9999999
            else :
                inventory_days = int(local_qty+amz_qty) / burn

            row.append(inventory_days)

            items_chart.append(row)


        top_bar.append(qty_total)
        top_bar.append(round(total_value,2))
        top_bar.append(sku_count)
        sell_through_rate = float(total_qty_sold) / float(total_qty_sold+qty_total)
        top_bar.append(sell_through_rate*100)


        return self.render('admin/inventory_index.html', items_chart=items_chart, top=top_bar)

class FBAView(BaseView):
    @expose('/',methods=(['GET']))
    @login_required
    def index(self):
        sb = StockBoy()
        formvalue = 45
        sb.DateFormController(formvalue)

        cursor = mongo.db.orders.find(
        {'$and':[
            {'orderDate':
                {'$lte': sb.results['end'],
                '$gte':sb.results['start']}
            },
            {'owner':sb.results['email']}
        ]}
        )
        sb.ReportDictBuilder(cursor)

        fba_dict = sb.results['fba_dict']

        ## Final Result
        sku_sales_dict = sb.results['sku_sales_dict']
        ## | UPC | Name | Amazon Qty (45 Days) | FBA Inventory | Days

        fba_inventory_results_dict = {}
        total_qty = 0
        fba_skus = len(fba_dict)


        for item in fba_dict :

            # Match ASIN with SKU
            sku = sb.SKUFlatten(item)
            # Get Amazon Only Sales from SKU Sales Dict
            if sku in sku_sales_dict :
                amz_qty = int(sku_sales_dict[sku]['amz-qty'])
                total_qty += amz_qty
                supply = int(fba_dict[item]['TotalSupplyQty'])
                amz_sales = sku_sales_dict[sku]['amz-sales']
                name = sku_sales_dict[sku]['name']
                img = sku_sales_dict[sku]['img']

                if supply == 0 :
                    days = 0
                elif amz_sales == 0 :
                    days = 9999999999
                else :
                    days = float(supply) / (float(amz_qty)/float(45))

            else :
                amz_qty = 0
                supply = int(fba_dict[item]['TotalSupplyQty'])
                days = 9999999999
                amz_sales = 0
                name = '??'
                img = '\#'

            fba_inventory_results_dict[sku] = {
                'sku' : sku,
                'asin' : item,
                'name' : name,
                'img' : img,
                'amz-sales' : amz_sales,
                'amz-qty': amz_qty,
                'supply' : supply,
                'days': days
            }

        sb.results['fba_inventory_results_dict'] = fba_inventory_results_dict
        sb.results['top_bar']['fba_skus'] = fba_skus
        sb.results['top_bar']['fba_total_qty'] = total_qty

        return self.render('admin/fba_index.html', results=sb.results)


class ProductView(BaseView):
    @expose('/',methods=('GET', 'POST'))
    @login_required
    #def is_visible(self):
        #return False
    def ProductIndex(self) :
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        start, end = DateFormHanlder(formvalue)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days
        date_dict, labels = DateDictBuilder(start, end)

        email = current_user.email
        ### Queries ###

        #Build Alias Dictionary - all owner alias values
        alias_search = mongo.db.alias.find({'Owner':email})
        alias_dict = AliasDictBuilder(alias_search)

        ## Find Owner Orders in Date Range
        range_search = mongo.db.orders.find({'$and':[
        {'orderDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})

        item_sku = 'All'
        item_chart, values, shipped_to_amz = ItemChartBuilder(range_search, date_dict, alias_dict, item_sku)

        top_bar = []
        sales_total = 0
        qty_total = 0
        # Build top bar values

        ## We do not want values per day, we want value per item, graphed
        sales_values = []
        qty_values = []
        labels = []
        item_chart = sorted(item_chart, key=lambda x: x[2], reverse=True)

        for row in item_chart :
            sales_total += row[2]
            if row[2] < 0:
                continue
            labels.append(row[1])
            qty_total += row[3]
            sales_values.append(row[2])
            qty_values.append(row[3])

        range_search.rewind()
        num_orders = range_search.count()
        avg_order_size = float(qty_total/num_orders)

        #### Placeholder for 2 chart handling
        values = sales_values
        ####

        top_bar.append(sales_total)
        top_bar.append(qty_total)
        top_bar.append(num_orders)
        top_bar.append(float(qty_total)/float(num_orders))
        max_values = max(sales_values)
        return self.render('admin/product_index.html',  top=top_bar,orders=item_chart, max=max_values, labels=labels, values=values, daterange=formvalue, startdate= start_date, enddate=end_date)


    @expose('/<string:target_sku>',methods=('GET', 'POST'))
    def Product(self, target_sku):
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        sb = StockBoy()

        sb.results['target_sku'] = target_sku

        sb.DateFormController(formvalue)

        cursor = mongo.db.orders.find(
        {'$and':[
            {'orderDate':
                {'$lte': sb.results['end'],
                '$gte':sb.results['start']}
            },
            {'owner':sb.results['email']},

        ]}
        )

        sb.ReportDictBuilder(cursor)

        sb.OrderedTogether(target_sku)

        results = sb.results
        #return self.render('admin/index.html', results=results)


        return self.render('admin/product_sku.html', results=results)

class CustomerView(BaseView) :
    @expose('/',methods=('GET', 'POST'))
    @login_required
    def CustomerIndex(self) :
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        ## Date range builder
        start, end = DateFormHanlder(formvalue)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days

        email = current_user.email
        order_value_list = []
        #Query - get orders for current users in date range
        range_search = mongo.db.orders.find({'$and':[
        {'orderDate':{'$lte': end, '$gte':start}},
        {'owner':email}]})
        num_orders = range_search.count()

        # Returns dictionary of orders, centered around customer details
        customer_dict = {}
        for order in range_search :
            if 'customerId' in order.keys():
                customer_id = order['customerId']
            else :
                customer_id = None

            if customer_id is None :
                # Amazon orders do not have a customer ID
                # [DONE] Create identifyable hash of the Street Address + Zip code
                # Create a customer ID with 'AMZ' prefix
                # Update MongoDB with both values
                address = order['shipTo']['street1']+'::'+order['shipTo']['postalCode']
                customer_id = base64.urlsafe_b64encode(hashlib.md5(address.encode('utf8')).digest())
                customer_id=customer_id.decode('ascii')

                #mongo.db.orders.update({'_id' : order['_id']},{'customerId':customer_id})

            if customer_id not in customer_dict :
                customer_dict.update({customer_id:{
                'num_orders': 0,
                'customer_value': 0
                }})

            customer_dict[customer_id]['name'] = order['shipTo']['name']
            customer_dict[customer_id]['city'] = order['shipTo']['city']
            customer_dict[customer_id]['state'] = order['shipTo']['state']
            customer_dict[customer_id]['email'] = order['customerEmail']
            customer_dict[customer_id]['num_orders'] +=1
            customer_dict[customer_id]['customer_value'] += order['orderTotal']
            order_value_list.append(order['orderTotal'])

        unique_customers = len(customer_dict)
        avg_order_value = sum(order_value_list) / len(order_value_list)
        repeat_rate = float(num_orders)/float(unique_customers)
        top_bar=[]
        top_bar.append(num_orders)
        top_bar.append(unique_customers)
        top_bar.append(avg_order_value)
        top_bar.append(repeat_rate)


        return self.render('admin/customer_index.html', customer_dict=customer_dict, top=top_bar, daterange=formvalue, startdate= start_date, enddate=end_date)

    @expose('/<customer_id>',methods=('GET', 'POST'))
    def CustomerDetails(self, customer_id):

        # Query orders for customer ID
        # Build sales & item chart based on query

        email = current_user.email
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
    def index(self):
        formvalue = False
        if request.method == 'POST':
            formvalue = request.form.get('daterange')

        start, end = DateFormHanlder(formvalue)
        start_date = start.strftime('%m/%d/%Y')
        end_date = end.strftime('%m/%d/%Y')
        delta_range = (end - start).days

        date_dict, labels = DateDictBuilder(start, end)

        ship_dict = ShipmentDictBuilder(start, end)

        shipment_range_order_ids = []
        for shipment in ship_dict :
            shipment_range_order_ids.append(ship_dict[shipment]['orderId'])

        order_cursor = mongo.db.orders.find({'orderId':{'$in':shipment_range_order_ids}})

        ## Order Dict Builder
        order_dict = {}
        for order in order_cursor :
            order_id = order['orderId']
            order_dict[order_id] = {}
            od = order_dict[order_id]

            od['orderDate'] = order['orderDate']
            order_qty = 0

            if 'items' in order.keys():
                for item in order['items']:
                    order_qty += item['quantity']

            od['orderQty'] = order_qty

        shipment_count = 0
        handle_times = []
        shipping_times = []
        shipment_chart = []
        shipment_costs = []
        qty_total = 0
        shipper_dict = ShipperDictBuilder()

        for shipment in ship_dict :
            row = []
            shipment_id = shipment

            shipper_id = ship_dict[shipment]['userId']
            shipper_name = shipper_dict[shipper_id]['name']
            row.append(shipper_name)

            order_id = ship_dict[shipment_id]['orderId']
            row.append(shipment_id)

            row.append(ship_dict[shipment]['name'])
            try:
                order_date = order_dict[order_id]['orderDate']
            except:
                flash(str(order_id) +" Not able to include this order for some reason")
                continue
            create_date = ship_dict[shipment]['createDate']

            handle_time = create_date - order_date
            # handle_time > 0 :
            days = handle_time.days
            seconds = handle_time.total_seconds()
            hours = seconds // 3600
            hours = hours - (days*24)
            minutes = (seconds % 3600) // 60
            seconds = seconds % 60
            handle_time_string = '{} days, {} hours'.format(days, hours)

            row.append(handle_time_string)

            if 'actual_delivery_date' in ship_dict[shipment].keys() :
                shipping_time = ship_dict[shipment]['actual_delivery_date'] - create_date
                shipping_times.append(shipping_time)
            else :
                shipping_time = 'NA'

            row.append(shipping_time)

            order_qty = order_dict[order_id]['orderQty']
            row.append(order_qty)
            qty_total += order_qty

            row.append(ship_dict[shipment]['shipmentCost'])
            shipment_costs.append(ship_dict[shipment]['shipmentCost'])

            row.append(shipper_id)
            shipment_count += 1
            shipment_chart.append(row)

            date_dict[create_date.year][create_date.month][create_date.day]+= 1

        values = []
        for year in date_dict.values() :
            for month in year.values() :
                for day in month.values() :
                    values.append(day)



        top_bar = []
        top_bar.append(shipment_count)

        if len(handle_times) > 0:
            average_handletime = sum(handle_times, timedelta(0)) / len(handle_times)
            #average_handletime = average_handletime.hours
            #days = handle_time.days
            seconds = average_handletime.total_seconds()
            hours = int(seconds) / 3600
            hours = hours - (days*24)
            #minutes = (seconds % 3600) // 60
            #seconds = seconds % 60
            avg_handle_time_string = '{} d, {} h'.format(days, hours)
        else :
            average_handletime = 0
            avg_handle_time_string = 'handle time'


        top_bar.append(avg_handle_time_string)
        if len(shipment_costs) > 0 :
            average_cost = sum(shipment_costs)/len(shipment_costs)

        else :
            average_cost = 0

        top_bar.append(average_cost)
        top_bar.append(qty_total/delta_range)

        return self.render('admin/shipment_index.html',  top=top_bar,orders=shipment_chart,  daterange=formvalue, startdate= start_date, enddate=end_date, labels=labels, values=values)




######################################
######## HOMEPAGE TO STATIC SITE ####
######################################
# Flask views
index_file_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'homepage')
assets_file_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'homepage/assets')

@app.route('/', methods=['GET'])
def homepage():
    return send_from_directory(index_file_dir, 'index.html')


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
    index_view=DashboardView(
    name='Dashboard',
    template='admin/index.html',
    menu_icon_type='fa',
    url = '/dashboard',
    menu_icon_value='fa-tachometer'
    )
    #index_view = DashboardView()

)
#################
# ADD ADMIN VIEWS
#################
#admin.add_view(InventoryEditor(Inventory,'Inventory',name='Inventory',menu_icon_type='fa',menu_icon_value='fa-archive'))
admin.add_view(SalesView(name="Sales", endpoint='sales', menu_icon_type='fa', menu_icon_value='fa-area-chart'))
admin.add_view(InventoryView(name="Inventory", endpoint='inventory', menu_icon_type='fa', menu_icon_value='fa-archive'))
admin.add_view(ProductView(name="Products", endpoint='product', menu_icon_type='fa', menu_icon_value='fa-shopping-bag'))
admin.add_view(CustomerView(name="Customers", endpoint='customers', menu_icon_type='fa', menu_icon_value='fa-users'))
admin.add_view(ShipmentView(name="Shipments", endpoint='shipments', menu_icon_type='fa', menu_icon_value='fa-truck'))
admin.add_view(FBAView(name="FBA", endpoint='fba', menu_icon_type='fa', menu_icon_value='fa-amazon'))
#admin.add_view(BurnView(name="Burn", endpoint='burn', menu_icon_type='fa', menu_icon_value='fa-free-code-camp'))
admin.add_view(ProfileView(name='Settings & Import', endpoint='import', menu_icon_type='fa', menu_icon_value='fa-cog'))

#admin.add_view(InventoryEditor(name='Inventory Editor', endpoint='inventory-editor', menu_icon_type='fa', menu_icon_value='fa-cog'))

## Sub Items - Not visible in menu
#admin.add_view(UserView(mongo.db['users'],mongo.db, endpoint='Mongo'))
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
    app.run(debug=True)
